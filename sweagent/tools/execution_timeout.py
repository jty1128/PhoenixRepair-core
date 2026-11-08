"""Choose a longer timeout for shell actions that invoke Git."""
import re
from pathlib import PurePosixPath

import bashlex


def invokes_command(command: str, name: str) -> bool:
    class Visitor(bashlex.ast.nodevisitor):
        found = False

        def visitcommand(self, node, parts):
            words = [p.word for p in parts if p.kind == 'word']
            if not words:
                return
            executable = PurePosixPath(words[0]).name
            if executable == name:
                self.found = True
            elif executable in {'xargs', 'env', 'command', 'exec', 'sudo', 'timeout', 'nice'}:
                self.found |= any(PurePosixPath(w).name == name for w in words[1:])
            elif executable in {'bash', 'sh', 'dash'}:
                for i, word in enumerate(words[1:], 1):
                    if word.startswith('-') and 'c' in word and i + 1 < len(words):
                        self.found |= invokes_command(words[i + 1], name)
                        break

    visitor = Visitor()
    try:
        for node in bashlex.parse(command):
            visitor.visit(node)
    except Exception:
        # bashlex does not support every Bash construct; timeout selection must
        # never prevent the runtime from handling the actual command.
        return visitor.found or bool(re.search(r'(?:^|[;&|\n(])\s*(?:[\w./-]+/)?' + re.escape(name) + r'(?:\s|$)', command))
    return visitor.found


def invokes_git(command: str) -> bool:
    return invokes_command(command, 'git')
