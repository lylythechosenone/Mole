#!/usr/bin/env python3
"""Check terminal restoration after leaving the real main menu."""
import fcntl
import os
import pty
import re
import select
import signal
import subprocess
import sys
import tempfile
import termios
import time
from pathlib import Path

root = Path(__file__).resolve().parents[1]


def check_exit(key):
    with tempfile.TemporaryDirectory(prefix="mole-menu-") as home:
        master, slave = pty.openpty()
        original = termios.tcgetattr(slave)
        env = dict(os.environ, HOME=home, TERM="xterm-256color",
                   MOLE_TEST_MODE="1", MOLE_SKIP_MAIN="1", MOLE_TEST_NO_AUTH="1",
                   TEST_ROOT=str(root))

        def own_terminal():
            os.setsid()
            fcntl.ioctl(0, termios.TIOCSCTTY, 0)

        child_script = 'source "$TEST_ROOT/mole"; interactive_main_menu'
        # Keep the controlling terminal alive after Bash exits so Darwin still
        # allows tcgetattr. The keeper must survive the same foreground Ctrl-C.
        keeper = (
            'import subprocess, time, signal; '
            'signal.signal(signal.SIGINT, lambda *args: None); '
            f'p = subprocess.run(["/bin/bash", "--noprofile", "--norc", "-c", {child_script!r}]); '
            'print("MENU_EXIT=" + str(p.returncode), flush=True); time.sleep(10)'
        )
        process = subprocess.Popen(
            [sys.executable, '-c', keeper],
            stdin=slave, stdout=slave, stderr=slave, env=env,
            preexec_fn=own_terminal)
        output = b''
        try:
            deadline = time.monotonic() + 10
            while b'Q Quit' not in output or termios.tcgetattr(slave)[3] & termios.ECHO:
                assert time.monotonic() < deadline, ('menu did not start reading', output)
                if select.select([master], [], [], .01)[0]:
                    output += os.read(master, 65536)
            os.write(master, key)
            deadline = time.monotonic() + 5
            while b'MENU_EXIT=' not in output and time.monotonic() < deadline:
                if select.select([master], [], [], .02)[0]:
                    output += os.read(master, 65536)
            assert b'MENU_EXIT=0' in output, ('exit failed', output[-3000:])
            restored = termios.tcgetattr(slave)
            # Darwin may set this transient input-reprocessing flag on reads.
            restored[3] &= ~getattr(termios, "PENDIN", 0)
            original[3] &= ~getattr(termios, "PENDIN", 0)
            assert restored == original, ('terminal settings changed', key, original, restored)
            print('PASS: main menu restores terminal after', repr(key))
        finally:
            if process.poll() is None:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except PermissionError:
                    process.kill()
                process.wait(timeout=5)
            os.close(slave)
            os.close(master)


check_exit(b'q')
check_exit(b'\x03')


def check_notice(script, expected_status=0, wait_marker=None, keys=b'', required=(), forbidden=(), parent_signal=None, repeat_signal=None):
    with tempfile.TemporaryDirectory(prefix="mole-update-terminal-") as home:
        master, slave = pty.openpty()
        env = dict(os.environ, HOME=home, TERM="xterm-256color", TEST_ROOT=str(root),
                   MOLE_TEST_MODE="1", MOLE_SKIP_MAIN="1", MOLE_TEST_NO_AUTH="1")
        child = 'source "$TEST_ROOT/mole"; mkdir -p "$HOME/.cache/mole"; ' + script
        keeper = ('import subprocess,time,signal; signal.signal(signal.SIGINT,lambda *a:None); '
                  f'p=subprocess.run(["/bin/bash","--noprofile","--norc","-c",{child!r}]); '
                  'print("CASE_EXIT="+str(p.returncode),flush=True); time.sleep(10)')

        def terminal():
            os.setsid()
            fcntl.ioctl(0, termios.TIOCSCTTY, 0)

        process = subprocess.Popen([sys.executable, '-c', keeper], stdin=slave,
                                   stdout=slave, stderr=slave, env=env, preexec_fn=terminal)
        output = b''
        sent = False
        repeated = False
        try:
            # Only hang detection: a loaded machine can delay sourcing mole and
            # the one-second child cleanup well past ten seconds.
            deadline = time.monotonic() + 30
            while b'CASE_EXIT=' not in output:
                assert time.monotonic() < deadline, ('terminal case timed out', output[-3000:])
                if select.select([master], [], [], .02)[0]:
                    output += os.read(master, 65536)
                if wait_marker and wait_marker in output and not sent:
                    if parent_signal:
                        pid = int(re.search(rb'WRAPPER_PID=(\d+)', output).group(1))
                        os.kill(pid, parent_signal)
                    else:
                        os.write(master, keys)
                    sent = True
                if repeat_signal and b'CLEANUP_STARTED' in output and not repeated:
                    os.kill(int(re.search(rb'WRAPPER_PID=(\d+)', output).group(1)), repeat_signal)
                    repeated = True
            assert f'CASE_EXIT={expected_status}\r\n'.encode() in output, output[-3000:]
            for marker in required:
                assert marker in output, (marker, output[-3000:])
            for marker in forbidden:
                assert marker not in output, (marker, output[-3000:])
            return output
        finally:
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait(timeout=5)
            os.close(slave)
            os.close(master)


# Use the actual asynchronous checker and actual key reader: no keypress should
# be needed before the delayed network result becomes visible.
check_notice('''
VERSION=1.0.0
get_install_channel(){ echo stable; }
is_homebrew_install(){ return 1; }
get_latest_version_from_github(){ sleep 1; echo 9.8.7; }
check_for_updates
interactive_main_menu
''', wait_marker=b'Update 9.8.7 available', keys=b'q', required=(b'U Update',))
print('PASS: idle menu refreshes a delayed update without a keypress')

setup = '''
check_for_updates(){ echo CHECK_CALLED; printf 'Update 9.8.7 available, run mo update' > "$HOME/.cache/mole/update_message"; }
SCRIPT_DIR="$HOME/commands"
mkdir -p "$SCRIPT_DIR/bin"
'''
for command in ['clean', 'optimize', 'uninstall', 'purge', 'installer', 'analyze', 'status']:
    script = setup + f'''
printf '#!/bin/bash\\nprintf "COMMAND_DONE\\\\n"\\nexit 7\\n' > "$SCRIPT_DIR/bin/{command}.sh"
chmod +x "$SCRIPT_DIR/bin/{command}.sh"
main {command}
'''
    output = check_notice(script, expected_status=7,
                          required=(b'CHECK_CALLED', b'COMMAND_DONE', b'Update 9.8.7 available'))
    assert output.index(b'COMMAND_DONE') < output.index(b'Update 9.8.7 available'), output
print('PASS: all seven interactive subcommands retain their status and show the notice after completion')

for flag in ['--json', '-json', '--json=true', '-json=true', '--ndjson', '--watch', '-watch', '--watch=true',
             '-watch=true', '--help', '-h', '--version', '-V', '--list', '--list=x']:
    check_notice(setup + f'''run_mole_command /bin/bash -c 'printf "JSON_OUTPUT\\n"' test {flag}''',
                 required=(b'JSON_OUTPUT',), forbidden=(b'CHECK_CALLED', b'Update 9.8.7'))
# Each of the three standard streams alone is enough to skip the notice. The
# all-terminal runs above are the positive control for CHECK_CALLED.
for redirect in ['> "$HOME/output"', '< /dev/null', '2> "$HOME/errors"']:
    check_notice(setup + f"""run_mole_command /bin/bash -c 'exit 0' {redirect}""",
                 forbidden=(b'CHECK_CALLED', b'Update 9.8.7'))
print('PASS: machine-readable flags, help, lists and any redirected stream stay silent')

check_notice(setup + '''run_mole_command /bin/bash -c 'trap "exit 130" INT; echo CHILD_READY; while :; do :; done' ''',
             expected_status=130, wait_marker=b'CHILD_READY', keys=b'\x03',
             forbidden=(b'Update 9.8.7',))
print('PASS: Ctrl-C preserves cancellation without printing an update notice')

for sig in [signal.SIGHUP, signal.SIGINT, signal.SIGTERM]:
    check_notice(setup + '''echo WRAPPER_PID=$$; run_mole_command /bin/bash -c 'trap "exit 130" INT; trap "exit 143" TERM; echo CHILD_READY; while :; do :; done' ''',
                 expected_status=128 + sig, wait_marker=b'CHILD_READY', parent_signal=sig,
                 forbidden=(b'Update 9.8.7',))
print('PASS: signals sent only to the router are forwarded to its child')
check_notice(setup + '''run_mole_command /bin/bash -c 'echo READ_READY; IFS= read -r text; echo GOT:$text' ''',
             wait_marker=b'READ_READY', keys=b'hello\n', required=(b'GOT:hello',))
print('PASS: interactive child keeps terminal stdin')

# The first signal fixes the status, whichever trap sees it and whichever signal
# follows; each pair would differ if one trap overwrote an earlier status.
for first, repeat in [(signal.SIGTERM, signal.SIGTERM), (signal.SIGTERM, signal.SIGINT), (signal.SIGTERM, signal.SIGHUP),
                      (signal.SIGINT, signal.SIGTERM), (signal.SIGHUP, signal.SIGTERM)]:
    output = check_notice(setup + """
cat > "$HOME/signal-child" <<'CHILD'
#!/bin/bash
cleanup() { trap '' TERM INT HUP; echo CLEANUP_STARTED; sleep 1; echo CLEANUP_DONE; exit 0; }
trap cleanup TERM INT HUP
echo CHILD_READY
while :; do :; done
CHILD
chmod +x "$HOME/signal-child"
echo WRAPPER_PID=$$
run_mole_command "$HOME/signal-child"
""",
                          expected_status=128 + first, wait_marker=b'CHILD_READY', parent_signal=first,
                          repeat_signal=repeat, required=(b'CLEANUP_DONE',), forbidden=(b'Update 9.8.7',))
    assert output.index(b'CLEANUP_DONE') < output.index(b'CASE_EXIT='), output
print('PASS: repeated signals preserve the first cancellation and wait for child cleanup')

# A target bash cannot launch keeps the diagnostic and status that a plain exec
# gives in the mole environment (the redirected-output path: 1, or 126 for a
# directory), instead of the perl wrapper's silent 127.
unlaunchable = setup + '''
printf '#!/bin/bash\\nexit 0\\n' > "$HOME/plain.sh"
chmod 644 "$HOME/plain.sh"
mkdir "$HOME/adir"
printf '#!/nonexistent/interp\\nexit 0\\n' > "$HOME/bad-interp.sh"
chmod 755 "$HOME/bad-interp.sh"
'''
for target, message, status in [('nope.sh', b'No such file or directory', 1), ('plain.sh', b'Permission denied', 1),
                                ('adir', b'is a directory', 126)]:
    check_notice(unlaunchable + f'run_mole_command "$HOME/{target}"', expected_status=status,
                 required=(message,), forbidden=(b'CHECK_CALLED', b'Update 9.8.7'))
# A #! line naming a missing interpreter is caught before the wrapper spawns
# anything, so bash names the interpreter ('bad interpreter') instead of the
# wrapper blaming the script, and no update check starts.
check_notice(unlaunchable + 'run_mole_command "$HOME/bad-interp.sh"', expected_status=1,
             required=(b'bad interpreter',), forbidden=(b'CHECK_CALLED', b'Update 9.8.7'))
print('PASS: an unlaunchable target keeps the diagnostic and status of a plain exec')

# Without /usr/bin/perl the command still runs through the plain exec path and
# only the notice is lost. The function text is rewritten to point at a missing
# perl, because the real one cannot be removed for a test.
check_notice(setup + """
eval "$(declare -f run_mole_command | sed 's|/usr/bin/perl|/nonexistent/perl|g')"
run_mole_command /bin/bash -c 'printf "COMMAND_DONE\\\\n"; exit 7'
""", expected_status=7, required=(b'COMMAND_DONE',), forbidden=(b'nonexistent', b'Update 9.8.7'))
print('PASS: a missing perl falls back to a plain exec with the command status')

# A child killed by a signal must not surface bash's job-status line, which
# quotes the internal perl one-liner; the status still reports the signal.
check_notice(setup + '''run_mole_command /bin/bash -c 'echo CHILD_READY; kill -KILL $$' ''',
             expected_status=137, required=(b'CHILD_READY',), forbidden=(b'perl', b'Killed', b'Update 9.8.7'))
print('PASS: a child killed by a signal keeps its status without bash job-status text')

# Delivery counts for INT. A terminal Ctrl-C reaches the child from the tty and
# again from the router's forward, so one or two deliveries are expected there;
# a signal sent to the router alone must arrive exactly once. The count child
# keeps running after a delivery so a late duplicate is still counted.
counter = setup + """
cat > "$HOME/count-child" <<'CHILD'
#!/bin/bash
n=0
trap 'n=$((n+1))' INT
echo CHILD_READY
end=$((SECONDS + 2))
while ((SECONDS < end)); do :; done
echo "INT_COUNT=$n"
CHILD
chmod +x "$HOME/count-child"
echo WRAPPER_PID=$$
run_mole_command "$HOME/count-child"
"""
output = check_notice(counter, expected_status=130, wait_marker=b'CHILD_READY', keys=b'\x03',
                      forbidden=(b'Update 9.8.7',))
count = re.search(rb'INT_COUNT=(\d+)', output)
assert count and int(count.group(1)) in (1, 2), output
output = check_notice(counter, expected_status=130, wait_marker=b'CHILD_READY', parent_signal=signal.SIGINT,
                      forbidden=(b'Update 9.8.7',))
assert b'INT_COUNT=1\r\n' in output, output
print(f'PASS: INT reaches the child once from the router and at most twice from a terminal Ctrl-C (saw {int(count.group(1))})')
