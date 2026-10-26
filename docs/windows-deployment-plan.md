# Windows/WSL deployment

The user selected the overnight Windows/WSL PC, identified the phone as a
OnePlus 13, permitted any test book, and confirmed that they can approve a
Windows administrator prompt. Neither physical device is currently connected.

Use a limited, interactive-user Windows task to keep a foreground `wsl.exe`
process and the Python service running. The task starts at logon and 05:50 local
time, requests wake, has no execution time limit, and ignores overlapping starts.
The coordinator retains its explicit 06:00 America/Toronto schedule.

Use WSL backend port 8788. One administrator setup adds a Windows port proxy
from port 8787 to Windows loopback port 8788, relying on WSL localhost forwarding.
The distinct ports avoid a forwarding loop and avoid a stale WSL VM address.
The Windows firewall exception permits TCP 8787 only from LocalSubnet. Do not
change global firewall policy, user account privileges, or the WSL network mode.

The elevated installer registers the task for the original Windows user SID
at Limited run level. It never launches that user's WSL distribution under a
different administrator account. Stage the installer/config in a native Windows
folder so an alternate administrator can read them during UAC. No password or
application pairing token is stored in the task definition.

Prepare the complete Peter Rabbit EPUB (Gutenberg 14838) and its corresponding
5m22s LibriVox recording (first section of project 15229), register the exact
files, transcribe locally, and check real passages without fabricating Kobo
observations in the live database. Stage a configured plugin and exact EPUB
for the eventual USB setup.

Work/checks:

1. Write and run failing PowerShell configuration/task-definition tests.
2. Implement config validation, launch arguments, task definition, Linux launcher,
   administrator installer and scoped uninstall. Run tests and parse both scripts.
3. Review the concrete installer before starting the administrator action.
4. Stage it on Windows, request the already-authorized UAC elevation, install and
   verify task state, actual listener health and the LAN endpoint.
5. Verify the real book's alignment and leave the service running for the devices.
6. Actual OnePlus/Kobo installation, reading-position capture and overnight
   acceptance remain required; host setup alone does not complete the goal.
