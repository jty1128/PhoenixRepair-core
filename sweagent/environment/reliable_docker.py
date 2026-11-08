"""Docker lifecycle with cleanup independent of the remote shell's health."""
import asyncio
import os
import subprocess

from swerex.deployment.docker import DockerDeployment


class ReliableDockerDeployment(DockerDeployment):
    _verified_start = False

    def __del__(self):
        # A managed host controller must capture the checkout after worker
        # exit, including exceptions and deadline shutdown. It owns cleanup.
        if not getattr(self, '_controller_managed', False):
            super().__del__()

    def _available_process_output(self, limit=16384):
        """Read only buffered bytes, never wait for EOF from a live Docker CLI."""
        process = self._container_process
        if process is None:
            return ''
        parts = []
        for name in ('stdout', 'stderr'):
            stream = getattr(process, name, None)
            if stream is None:
                continue
            chunks = []
            try:
                fd = stream.fileno()
                blocking = os.get_blocking(fd)
                os.set_blocking(fd, False)
                try:
                    remaining = limit
                    while remaining:
                        try:
                            chunk = os.read(fd, min(4096, remaining))
                        except BlockingIOError:
                            break
                        if not chunk:
                            break
                        chunks.append(chunk)
                        remaining -= len(chunk)
                finally:
                    os.set_blocking(fd, blocking)
            except (OSError, ValueError):
                pass
            parts.append(name + ':\n' + b''.join(chunks).decode(errors='replace'))
        return '\n'.join(parts)

    async def is_alive(self, *, timeout=None):
        if self._runtime is None or self._container_process is None:
            raise RuntimeError('Runtime not started')
        if self._container_process.poll() is not None:
            raise RuntimeError('Container process terminated.\n' + self._available_process_output())
        return await self._runtime.is_alive(timeout=timeout)

    async def _wait_until_alive(self, timeout=10.0):
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        while (remaining := deadline - loop.time()) > 0:
            budget = min(self._runtime_timeout, remaining)
            try:
                if await asyncio.wait_for(self.is_alive(timeout=budget), timeout=budget):
                    return
            except asyncio.TimeoutError:
                pass
            await asyncio.sleep(min(0.25, max(0, deadline - loop.time())))
        self.logger.error('Runtime startup timed out; available container output:\n%s',
                          self._available_process_output())
        # start() owns cleanup. Do not read until EOF or clean up twice here.
        raise TimeoutError(f'Runtime startup timed out after {timeout}s')

    async def start(self):
        self._verified_start = False
        try:
            await super().start()
            self._verified_start = True
        except BaseException as original:
            # Never send /close to an unverified listener (possibly an old
            # container on a conflicting port). Only remove our own container.
            try:
                await self.stop()
            except Exception as cleanup_error:
                self.logger.warning('Startup cleanup failed: %s', cleanup_error)
                original.add_note('Startup cleanup failed: ' + str(cleanup_error))
            raise

    def _remove_owned_container(self):
        if not self._container_name:
            return
        runtime = self._config.container_runtime
        result = subprocess.run([runtime, 'rm', '-f', self._container_name],
                                capture_output=True, text=True, timeout=30)
        if result.returncode and 'No such container' not in result.stderr:
            raise RuntimeError('Failed to remove owned container: ' + result.stderr)
        self._container_name = None

    async def stop(self):
        runtime, self._runtime = self._runtime, None
        try:
            if runtime is not None and self._verified_start:
                try:
                    await asyncio.wait_for(runtime.close(), timeout=10)
                except Exception as exc:
                    self.logger.warning('Remote shell close failed; removing owned container: %s', exc)
        finally:
            # A failed /close must never skip container removal.
            try:
                await asyncio.to_thread(self._remove_owned_container)
            finally:
                # Docker rm can itself time out. Always reap the local client.
                self._verified_start = False
                process, self._container_process = self._container_process, None
                if process is not None:
                    try:
                        try:
                            await asyncio.to_thread(process.wait, timeout=5)
                        except subprocess.TimeoutExpired:
                            process.kill()
                            await asyncio.to_thread(process.wait, timeout=5)
                    finally:
                        for stream in (process.stdout, process.stderr):
                            if stream is not None:
                                stream.close()
