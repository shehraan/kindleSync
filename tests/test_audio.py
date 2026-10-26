import tempfile
import unittest
from pathlib import Path
from urllib.error import HTTPError
from unittest.mock import patch


class AudioTests(unittest.TestCase):
    def test_catalog_no_results_is_an_empty_list(self):
        from kobosync.audio import search_librivox
        with patch("kobosync.audio.fetch_json", side_effect=HTTPError("https://librivox.org/",404,"No results",{},None)):
            self.assertEqual(search_librivox("not a real book"), [])

    def test_librivox_tracks_are_downloaded_in_numeric_order(self):
        from kobosync.audio import fetch_librivox
        fixture={"sections":[{"section_number":"10","listen_url":"https://archive.org/ten.mp3"},{"section_number":"2","listen_url":"https://archive.org/two.mp3"}]}
        with tempfile.TemporaryDirectory() as tmp, patch("kobosync.audio.fetch_json",return_value=fixture), patch("kobosync.audio.download") as download:
            paths=fetch_librivox("52",tmp)
            self.assertEqual(download.call_args_list[0].args[0],"https://archive.org/two.mp3")
            self.assertEqual(paths,[Path(tmp)/"0000.mp3",Path(tmp)/"0001.mp3"])
