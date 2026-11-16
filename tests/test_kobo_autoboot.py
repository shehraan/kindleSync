"""Exercise the real startup scripts against a temporary Kobo filesystem."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import zipfile


SOURCE = Path(__file__).resolve().parents[1] / 'deploy' / 'kobo-autoboot'
ORIGINAL = '#!/bin/sh\n# Original Kobo boot animation\nprintf "animation\\n"\n'


class KoboAutobootTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.onboard = self.root / 'mnt/onboard'
        self.control = self.onboard / '.adds/kobosync-autoboot'
        self.reader = self.onboard / '.adds/koreader'
        self.installed = self.root / 'usr/local/kobosync-autoboot'
        self.animator = self.root / 'etc/init.d/on-animator.sh'
        self.write(self.animator, ORIGINAL, 0o755)
        self.write(self.onboard / '.kobo/version', 'test,4.1.15,4.38.23828,0000000388\n')
        self.write(self.root / 'proc/mounts', f'/dev/mmcblk0p3 {self.onboard} vfat rw,relatime 0 0\n')
        self.write(self.root / 'sys/class/power_supply/mc13892_bat/status', 'Discharging\n')
        self.write(self.reader / 'reader.lua', 'return {}\n')
        self.write(self.reader / 'luajit', '#!/bin/sh\nexit 0\n', 0o755)
        self.launcher('sleep')
        self.control.mkdir(parents=True, exist_ok=True)
        (self.control / 'enabled').touch()
        self.bin = self.root / 'bin'
        self.write(self.bin / 'pidof', '#!/bin/sh\n[ "$1" = nickel ] && [ ! -e "$KOBOSYNC_BOOT_ROOT/no-nickel" ] && printf "123\\n"\n', 0o755)
        self.write(self.bin / 'sleep', '#!/bin/sh\n/bin/sleep .01\n', 0o755)
        self.env = dict(os.environ, KOBOSYNC_BOOT_ROOT=str(self.root),
                        KOBOSYNC_BOOT_TIMEOUT='3', KOBOSYNC_BOOT_SETTLE='0',
                        KOBOSYNC_BOOT_STABLE='3', PATH=str(self.bin)+':'+os.environ['PATH'])

    @staticmethod
    def write(path, contents, mode=0o644):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(contents)
        path.chmod(mode)

    def launcher(self, mode):
        ending = {'sleep': '/bin/sleep .1\nexit 0\n', 'clean': 'exit 0\n', 'crash': 'exit 7\n'}[mode]
        self.write(self.reader / 'koreader.sh', '#!/bin/sh\nprintf "launch\\n" >> "$KOBOSYNC_BOOT_ROOT/launched"\n'+ending, 0o755)

    def run_script(self, name, *args, expected=0):
        result = subprocess.run(['/bin/sh', str(SOURCE / name), *args], env=self.env,
                                capture_output=True, text=True, timeout=5)
        self.assertEqual(result.returncode, expected, result.stdout+result.stderr)
        return result

    def launches(self):
        path = self.root / 'launched'
        return len(path.read_text().splitlines()) if path.exists() else 0

    def boot(self):
        self.run_script('autostart.sh')

    def test_enable_backs_up_exact_original_and_preserves_permissions(self):
        self.run_script('manage.sh', 'enable')
        self.assertEqual((self.installed / 'on-animator.original').read_text(), ORIGINAL)
        self.assertEqual(self.animator.stat().st_mode & 0o777, 0o755)
        content = self.animator.read_text()
        self.assertTrue(content.startswith('#!/bin/sh\n'))
        self.assertIn(ORIGINAL.split('\n', 1)[1], content)
        self.assertIn('/usr/local/kobosync-autoboot/autostart.sh', content)
        self.assertTrue((self.installed / 'autostart.sh').exists())

    def test_enable_is_idempotent_and_disable_restores_original(self):
        self.run_script('manage.sh', 'enable')
        first = self.animator.read_bytes()
        self.run_script('manage.sh', 'enable')
        self.assertEqual(first, self.animator.read_bytes())
        self.run_script('manage.sh', 'disable')
        self.assertEqual(self.animator.read_text(), ORIGINAL)
        self.assertFalse((self.control / 'enabled').exists())
        self.run_script('manage.sh', 'disable')
        self.assertEqual(self.animator.read_text(), ORIGINAL)

    def test_disable_leaves_later_changes_intact_but_stops_autostart(self):
        self.run_script('manage.sh', 'enable')
        changed = self.animator.read_text()+'# Another mod\n'
        self.animator.write_text(changed)
        self.run_script('manage.sh', 'disable')
        self.assertEqual(self.animator.read_text(), changed)
        self.assertFalse((self.control / 'enabled').exists())

    def test_reenable_refuses_changed_startup_without_overwriting_backup(self):
        self.run_script('manage.sh', 'enable')
        self.animator.write_text(self.animator.read_text()+'# Changed\n')
        self.run_script('manage.sh', 'enable', expected=1)
        self.assertEqual((self.installed / 'on-animator.original').read_text(), ORIGINAL)
        self.assertIn('# Changed', self.animator.read_text())

    def test_disabled_install_can_be_enabled_again_without_losing_backup(self):
        self.run_script('manage.sh', 'enable')
        self.run_script('manage.sh', 'disable')
        self.run_script('manage.sh', 'enable')
        self.assertEqual((self.installed / 'on-animator.original').read_text(), ORIGINAL)
        self.assertTrue((self.control / 'enabled').exists())

    def test_corrupt_backup_is_not_restored_to_the_boot_path(self):
        self.run_script('manage.sh', 'enable')
        before = self.animator.read_bytes()
        (self.installed / 'on-animator.original').write_text('#!/bin/sh\nexit 1\n')
        self.run_script('manage.sh', 'disable', expected=1)
        self.assertEqual(self.animator.read_bytes(), before)
        self.assertFalse((self.control / 'enabled').exists())

    def test_invalid_payload_does_not_change_startup(self):
        self.write(self.control / 'manage.sh', (SOURCE / 'manage.sh').read_text())
        self.write(self.control / 'autostart.sh', 'if malformed shell\n')
        result = subprocess.run(['/bin/sh', str(self.control / 'manage.sh'), 'enable'],
                                env=self.env, capture_output=True, text=True, timeout=5)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.animator.read_text(), ORIGINAL)
        self.assertFalse(self.installed.exists())

    def test_wrong_device_or_firmware_does_not_change_startup(self):
        for version in ['test,4.1.15,4.38.23828,0000000376\n', 'test,4.1.15,5.18.1,0000000388\n']:
            with self.subTest(version=version):
                (self.onboard / '.kobo/version').write_text(version)
                self.run_script('manage.sh', 'enable', expected=1)
                self.assertEqual(self.animator.read_text(), ORIGINAL)
                self.assertFalse(self.installed.exists())

    def test_unknown_argument_cannot_modify_startup(self):
        self.run_script('manage.sh', 'erase', expected=1)
        self.assertEqual(self.animator.read_text(), ORIGINAL)
        self.assertFalse(self.installed.exists())

    def test_status_is_read_only(self):
        before = {p.relative_to(self.root): p.read_bytes() for p in self.root.rglob('*') if p.is_file()}
        self.run_script('manage.sh', 'status')
        after = {p.relative_to(self.root): p.read_bytes() for p in self.root.rglob('*') if p.is_file()}
        self.assertEqual(before, after)

    def test_boot_launches_once_and_clears_success_marker(self):
        self.boot()
        self.assertEqual(self.launches(), 1)
        self.assertFalse((self.control / 'pending').exists())
        self.boot()
        self.assertEqual(self.launches(), 1)

    def test_disabled_boot_leaves_nickel_running(self):
        (self.control / 'enabled').unlink()
        self.boot()
        self.assertEqual(self.launches(), 0)

    def test_power_cable_at_boot_skips_autostart_even_if_full(self):
        for state in ['Charging', 'Full']:
            with self.subTest(state=state):
                self.write(self.root / 'sys/class/power_supply/mc13892_bat/status', state+'\n')
                self.boot()
                self.assertEqual(self.launches(), 0)
                lock = self.root / 'tmp/kobosync-autoboot.attempted'
                if lock.exists(): lock.rmdir()

    def test_usb_supply_and_configured_gadget_skip_autostart(self):
        self.write(self.root / 'sys/class/power_supply/usb/type', 'USB\n')
        self.write(self.root / 'sys/class/power_supply/usb/online', '1\n')
        self.boot()
        self.assertEqual(self.launches(), 0)
        (self.root / 'tmp/kobosync-autoboot.attempted').rmdir()
        (self.root / 'sys/class/power_supply/usb/online').write_text('0\n')
        self.write(self.root / 'sys/class/udc/controller/state', 'configured\n')
        self.boot()
        self.assertEqual(self.launches(), 0)

    def test_unattached_usb_controller_allows_autostart(self):
        self.write(self.root / 'sys/class/udc/controller/state', 'not attached\n')
        self.boot()
        self.assertEqual(self.launches(), 1)

    def test_unknown_power_status_keeps_recovery_available(self):
        (self.root / 'sys/class/power_supply/mc13892_bat/status').unlink()
        self.boot()
        self.assertEqual(self.launches(), 0)

    def test_readonly_or_unmounted_onboard_never_launches(self):
        (self.root / 'proc/mounts').write_text(f'/dev/mmcblk0p3 {self.onboard} vfat ro,relatime 0 0\n')
        self.boot()
        self.assertEqual(self.launches(), 0)

    def test_absent_nickel_times_out_without_a_launch(self):
        (self.root / 'no-nickel').touch()
        self.boot()
        self.assertEqual(self.launches(), 0)

    def test_incomplete_reader_is_not_launched(self):
        (self.reader / 'luajit').unlink()
        self.boot()
        self.assertEqual(self.launches(), 0)

    def test_new_firmware_prevents_existing_hook_from_launching(self):
        (self.onboard / '.kobo/version').write_text('test,4.1.15,5.18.1,0000000388\n')
        self.boot()
        self.assertEqual(self.launches(), 0)

    def test_crash_keeps_pending_marker_and_next_boot_stays_in_nickel(self):
        self.launcher('crash')
        self.boot()
        self.assertEqual(self.launches(), 1)
        self.assertTrue((self.control / 'pending').exists())
        (self.root / 'tmp/kobosync-autoboot.attempted').rmdir()
        self.launcher('sleep')
        self.boot()
        self.assertEqual(self.launches(), 1)

    def test_clean_early_exit_does_not_disable_future_boots(self):
        self.launcher('clean')
        self.boot()
        self.assertEqual(self.launches(), 1)
        self.assertFalse((self.control / 'pending').exists())

    def test_enable_clears_pending_for_an_intentional_retry(self):
        (self.control / 'pending').touch()
        self.run_script('manage.sh', 'enable')
        self.assertFalse((self.control / 'pending').exists())

    def test_saved_reading_data_is_untouched(self):
        settings = self.reader / 'settings.reader.lua'
        queue = self.reader / 'settings/kobosync.lua'
        self.write(settings, 'return { lastfile = "my-book.epub" }\n')
        self.write(queue, 'return { pending = { "preserve me" } }\n')
        before = settings.read_bytes(), queue.read_bytes()
        self.run_script('manage.sh', 'enable')
        self.boot()
        self.run_script('manage.sh', 'disable')
        self.assertEqual(before, (settings.read_bytes(), queue.read_bytes()))

    def test_package_contains_only_component_files_with_matching_hashes(self):
        import hashlib
        archive = self.root / 'autoboot.zip'
        result = subprocess.run([sys.executable, str(SOURCE / 'build.py'), '--output', str(archive)],
                                capture_output=True, text=True, timeout=5)
        self.assertEqual(result.returncode, 0, result.stdout+result.stderr)
        with zipfile.ZipFile(archive) as bundle:
            prefix = '.adds/kobosync-autoboot/'
            self.assertEqual(set(bundle.namelist()), {prefix+'manage.sh', prefix+'autostart.sh',
                                                      prefix+'manifest.json', '.adds/nm/kobosync-autoboot'})
            manifest = json.loads(bundle.read(prefix+'manifest.json'))
            for path, digest in manifest['files'].items():
                self.assertEqual(hashlib.sha256(bundle.read(path)).hexdigest(), digest)
            self.assertNotIn(b'\r', bundle.read(prefix+'manage.sh'))
            self.assertNotIn(b'\r', bundle.read(prefix+'autostart.sh'))


if __name__ == '__main__':
    unittest.main()
