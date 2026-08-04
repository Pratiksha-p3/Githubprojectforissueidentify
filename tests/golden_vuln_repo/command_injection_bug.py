# Golden fixture: subprocess call with shell=True and a dynamic command.
# Expected: analyzers.command_injection_checker fires.

import subprocess


def run(user_command):
    subprocess.run(user_command, shell=True)
