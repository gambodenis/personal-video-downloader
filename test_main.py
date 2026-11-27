import unittest
from pathlib import Path

from main import (
    build_choices,
    codec_name,
    download_output_template,
    explain_download_error,
    format_duration,
    format_size,
    make_youtube_options,
)

FORMATS = [
    {"format_id": "sb0", "ext": "mhtml", "vcodec": "none", "acodec": "none"},
    {"format_id": "140", "ext": "m4a", "vcodec": "none", "acodec": "mp4a.40.2", "abr": 129, "filesize": 1000},
    {"format_id": "251", "ext": "webm", "vcodec": "none", "acodec": "opus", "abr": 137, "filesize": 1100},
    {"format_id": "18", "ext": "mp4", "height": 360, "width": 640, "fps": 25,
     "vcodec": "avc1.42001E", "acodec": "mp4a.40.2", "tbr": 246, "protocol": "https"},
    {"format_id": "137", "ext": "mp4", "height": 1080, "width": 1920, "fps": 25,
     "vcodec": "avc1.640028", "acodec": "none", "tbr": 1592, "filesize": 4000, "protocol": "https"},
    {"format_id": "248", "ext": "webm", "height": 1080, "width": 1920, "fps": 25,
     "vcodec": "vp9", "acodec": "none", "tbr": 773, "protocol": "https"},
    {"format_id": "270", "ext": "mp4", "height": 1080, "width": 1920, "fps": 25,
     "vcodec": "avc1.640028", "acodec": "none", "tbr": 4761, "protocol": "m3u8_native"},
]


class HelperTests(unittest.TestCase):
    def test_format_size_formats_bytes(self) -> None:
        self.assertEqual(format_size(1536), "1.5 Ko")

    def test_format_size_handles_unknown_size(self) -> None:
        self.assertEqual(format_size(None), "Inconnue")

    def test_format_duration(self) -> None:
        self.assertEqual(format_duration(75), "1:15")
        self.assertEqual(format_duration(3725), "1:02:05")

    def test_codec_names(self) -> None:
        self.assertEqual(codec_name("avc1.640028"), "H.264")
        self.assertEqual(codec_name("vp09.00.40.08"), "VP9")
        self.assertEqual(codec_name("av01.0.08M.08"), "AV1")

    def test_browser_session_is_opt_in(self) -> None:
        self.assertNotIn("cookiesfrombrowser", make_youtube_options("Aucune"))
        self.assertEqual(make_youtube_options("Edge")["cookiesfrombrowser"], ("edge",))

    def test_js_runtime_is_passed_to_yt_dlp(self) -> None:
        runtimes = {"node": {"path": "node.exe"}}
        self.assertEqual(make_youtube_options("Aucune", runtimes)["js_runtimes"], runtimes)
        self.assertNotIn("js_runtimes", make_youtube_options("Aucune", {}))

    def test_video_download_filename_includes_format_identifier(self) -> None:
        template = download_output_template(Path("downloads"), {"kind": "video"})
        self.assertIn("%(height)sp", template)
        self.assertIn("%(format_id)s", template)

    def test_audio_download_filename_includes_format_identifier(self) -> None:
        template = download_output_template(Path("downloads"), {"kind": "audio"})
        self.assertNotIn("%(height)sp", template)
        self.assertIn("%(format_id)s", template)


class ChoiceTests(unittest.TestCase):
    def test_curated_list_has_one_row_per_height_preferring_h264_direct(self) -> None:
        choices = build_choices(FORMATS, has_ffmpeg=True)
        kinds = [c["kind"] for c in choices]
        self.assertEqual(kinds, ["auto", "video", "video", "audio"])
        self.assertEqual(choices[1]["format_id"], "137")
        self.assertEqual(choices[1]["quality"], "1080p")
        self.assertEqual(choices[2]["format_id"], "18")
        self.assertEqual(choices[2]["audio"], "Inclus")

    def test_video_only_format_is_merged_with_audio(self) -> None:
        choice = build_choices(FORMATS, has_ffmpeg=True)[1]
        self.assertTrue(choice["selector"].startswith("137+ba[ext=m4a]"))
        self.assertEqual(choice["merge"], "mp4/mkv")
        self.assertEqual(choice["size"], format_size(5000))

    def test_missing_ffmpeg_is_flagged(self) -> None:
        choice = build_choices(FORMATS, has_ffmpeg=False)[1]
        self.assertIn("FFmpeg", choice["audio"])

    def test_show_all_lists_every_video_format_without_storyboards(self) -> None:
        ids = {c.get("format_id") for c in build_choices(FORMATS, True, show_all=True)}
        self.assertTrue({"18", "137", "248", "270"} <= ids)
        self.assertNotIn("sb0", ids)


class ErrorTests(unittest.TestCase):
    def test_http_403_has_actionable_guidance(self) -> None:
        guidance = explain_download_error("HTTP Error 403: Forbidden")
        self.assertIn("actualisez les formats", guidance.lower())
        self.assertIn("Session YouTube", guidance)

    def test_unavailable_video_is_explained(self) -> None:
        guidance = explain_download_error("This video is unavailable")
        self.assertIn("indisponible", guidance)
        self.assertIn("privée", guidance)


if __name__ == "__main__":
    unittest.main()
