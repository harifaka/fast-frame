"""Unit tests for the FFmpeg command builder (stdlib unittest, no ffmpeg needed)."""

from __future__ import annotations

import unittest
from pathlib import Path

from services.ffmpeg_builder import (
    FFmpegBuildError,
    RenderOptions,
    build_atempo_chain,
    build_crop_filter,
    build_ffmpeg_command,
    build_filter_graph,
    build_transform_filters,
    compute_crop_pixels,
    escape_subtitle_path,
    normalize_segments,
    target_duration,
)


def make_options(**overrides) -> RenderOptions:
    values = {
        "input_path": "/media/in.mp4",
        "output_path": "/media/out.mp4",
        "speed": 2.0,
        "codec": "h264",
        "has_audio": True,
        "source_width": 1920,
        "source_height": 1080,
        "source_duration": 120.0,
        "h264_encoder": "h264_nvenc",
        "hevc_encoder": "hevc_nvenc",
        "ffmpeg_major": 5,
    }
    values.update(overrides)
    return RenderOptions(**values)


def filter_string(options: RenderOptions) -> str:
    return build_filter_graph(options).filter_complex


class AtempoChainTests(unittest.TestCase):
    def test_unity_speed_has_no_filter(self) -> None:
        self.assertIsNone(build_atempo_chain(1.0))

    def test_double_speed_is_single_filter(self) -> None:
        self.assertEqual(build_atempo_chain(2.0), "atempo=2")

    def test_triple_speed_chains_two_filters(self) -> None:
        self.assertEqual(build_atempo_chain(3.0), "atempo=2,atempo=1.5")

    def test_quadruple_speed_chains_two_filters(self) -> None:
        self.assertEqual(build_atempo_chain(4.0), "atempo=2,atempo=2")

    def test_quarter_speed_chains_two_filters(self) -> None:
        self.assertEqual(build_atempo_chain(0.25), "atempo=0.5,atempo=0.5")

    def test_sub_unit_speed_stays_within_bounds(self) -> None:
        self.assertEqual(build_atempo_chain(0.75), "atempo=0.75")

    def test_all_factors_stay_inside_atempo_bounds(self) -> None:
        for speed in (0.25, 0.3, 0.5, 0.8, 1.3, 2.0, 2.5, 3.0, 4.0):
            chain = build_atempo_chain(speed)
            factors = [float(part.split("=")[1]) for part in chain.split(",")]
            for factor in factors:
                self.assertGreaterEqual(factor, 0.5 - 1e-9)
                self.assertLessEqual(factor, 2.0 + 1e-9)
            product = 1.0
            for factor in factors:
                product *= factor
            self.assertAlmostEqual(product, speed, places=4)

    def test_invalid_speeds_raise(self) -> None:
        with self.assertRaises(FFmpegBuildError):
            build_atempo_chain(0)


class FilterGraphTests(unittest.TestCase):
    def test_speed_filters_video_and_audio(self) -> None:
        graph = build_filter_graph(make_options(speed=1.5))
        self.assertIn("[0:v:0]setpts=PTS/1.5[v_speed]", graph.statements)
        self.assertIn("[0:a:0]atempo=1.5[a_speed]", graph.statements)
        self.assertEqual(graph.video_label, "v_speed")
        self.assertEqual(graph.audio_label, "a_speed")
        self.assertTrue(graph.timing_changed)

    def test_silent_video_has_no_audio_filter(self) -> None:
        graph = build_filter_graph(make_options(has_audio=False))
        self.assertFalse(any("atempo" in line for line in graph.statements))
        self.assertIsNone(graph.audio_label)
        command = build_ffmpeg_command(make_options(has_audio=False))
        self.assertIn("-an", command)
        self.assertNotIn("-c:a", command)

    def test_identical_speed_produces_no_graph(self) -> None:
        graph = build_filter_graph(make_options(speed=1.0))
        self.assertEqual(graph.statements, [])
        self.assertFalse(graph)
        command = build_ffmpeg_command(make_options(speed=1.0))
        self.assertNotIn("-filter_complex", command)

    def test_multi_cut_trims_concats_then_speeds(self) -> None:
        options = make_options(
            speed=1.5,
            segments=[{"start": 10, "end": 45}, {"start": 60, "end": 120}],
        )
        graph = build_filter_graph(options)
        joined = graph.filter_complex

        self.assertIn("[0:v:0]trim=start=10:end=45,setpts=PTS-STARTPTS[v0]", joined)
        self.assertIn("[0:a:0]atrim=start=10:end=45,asetpts=PTS-STARTPTS[a0]", joined)
        self.assertIn("[0:v:0]trim=start=60:end=120,setpts=PTS-STARTPTS[v1]", joined)
        self.assertIn(
            "[v0][a0][v1][a1]concat=n=2:v=1:a=1[v_cat][a_cat]",
            joined,
        )
        self.assertLess(joined.index("trim=start=10"), joined.index("concat="))
        self.assertLess(joined.index("concat="), joined.index("setpts=PTS/1.5"))
        self.assertEqual(graph.audio_label, "a_speed")

    def test_single_segment_skips_concat(self) -> None:
        graph = build_filter_graph(
            make_options(speed=1.0, segments=[{"start": 5, "end": 10}])
        )
        self.assertNotIn("concat=", graph.filter_complex)
        self.assertEqual(graph.video_label, "v0")
        self.assertEqual(graph.audio_label, "a0")

    def test_silent_multi_cut_concat_has_no_audio(self) -> None:
        graph = build_filter_graph(
            make_options(
                has_audio=False,
                speed=1.0,
                segments=[{"start": 1, "end": 2}, {"start": 3, "end": 4}],
            )
        )
        self.assertIn("[v0][v1]concat=n=2:v=1:a=0[v_cat]", graph.filter_complex)
        self.assertNotIn("atrim", graph.filter_complex)
        self.assertIsNone(graph.audio_label)

    def test_segments_are_clamped_to_source_duration(self) -> None:
        segments = normalize_segments(
            [{"start": 100, "end": 300}, {"start": 0, "end": 10}], source_duration=200
        )
        self.assertEqual(segments, [{"start": 0, "end": 10}, {"start": 100, "end": 200}])

    def test_invalid_segment_order_rejected(self) -> None:
        with self.assertRaises(FFmpegBuildError):
            normalize_segments([{"start": 10, "end": 10}])

    def test_subtitles_are_burned_before_trimming(self) -> None:
        options = make_options(
            speed=1.5,
            segments=[{"start": 10, "end": 45}],
            subtitle_path="/media/subtitles/movie.en.srt",
            subtitle_font_size=32,
        )
        graph = build_filter_graph(options)
        first = graph.statements[0]
        self.assertTrue(first.startswith("[0:v:0]subtitles=filename='/media/subtitles/movie.en.srt'"))
        self.assertIn("FontSize=32", first)
        self.assertIn("trim=start=10", graph.statements[1])
        self.assertLess(
            graph.filter_complex.index("subtitles="), graph.filter_complex.index("trim=")
        )
        # Subtitles must be applied before the speed filter so 1x timestamps match.
        self.assertLess(
            graph.filter_complex.index("subtitles="), graph.filter_complex.index("setpts=PTS/1.5")
        )

    def test_subtitle_paths_with_special_characters_are_escaped(self) -> None:
        escaped = escape_subtitle_path("C:\\Media\\My Videos\\Episode: 1's [final].srt")
        self.assertEqual(
            escaped, "C\\:/Media/My Videos/Episode\\: 1\\'s \\[final\\].srt"
        )

    def test_target_duration_accounts_for_cuts_and_speed(self) -> None:
        options = make_options(
            speed=2.0,
            segments=[{"start": 10.0, "end": 40.0}, {"start": 60.0, "end": 100.0}],
        )
        self.assertAlmostEqual(target_duration(options), 35.0, places=6)

    def test_target_duration_without_segments_uses_source(self) -> None:
        self.assertAlmostEqual(target_duration(make_options(speed=2.0)), 60.0, places=6)


class TransformTests(unittest.TestCase):
    def test_rotation_filters(self) -> None:
        cases = {
            "none": [],
            "90_cw": ["transpose=1"],
            "90_ccw": ["transpose=2"],
            "180": ["transpose=1", "transpose=1"],
        }
        for rotation, expected in cases.items():
            options = make_options(speed=1.0, rotation=rotation)
            self.assertEqual(build_transform_filters(options), expected)

    def test_flip_is_applied_after_rotation(self) -> None:
        options = make_options(speed=1.0, rotation="90_cw", flip_horizontal=True)
        self.assertEqual(build_transform_filters(options), ["transpose=1", "hflip"])
        graph = build_filter_graph(options)
        self.assertIn("[0:v:0]transpose=1,hflip[v_transform]", graph.filter_complex)

    def test_crop_uses_oriented_dimensions_after_rotation(self) -> None:
        options = make_options(
            speed=1.0,
            source_width=1920,
            source_height=1080,
            rotation="90_cw",
            crop={"x": 0.1, "y": 0.1, "w": 0.8, "h": 0.8},
        )
        # 90° swaps the frame to 1080×1920 before cropping.
        pixels = compute_crop_pixels(options)
        self.assertEqual(pixels, (864, 1536, 108, 192))
        self.assertIn("crop=864:1536:108:192", build_crop_filter(options))

    def test_crop_coordinates_are_even(self) -> None:
        options = make_options(
            speed=1.0,
            source_width=1000,
            source_height=600,
            crop={"x": 0.1, "y": 0.1, "w": 0.333, "h": 0.333},
        )
        crop_w, crop_h, crop_x, crop_y = compute_crop_pixels(options)
        for value in (crop_w, crop_h, crop_x, crop_y):
            self.assertEqual(value % 2, 0)
        self.assertEqual(crop_w, 332)
        self.assertEqual(crop_h, 200)

    def test_crop_is_clamped_inside_frame(self) -> None:
        options = make_options(
            speed=1.0,
            source_width=1000,
            source_height=1000,
            crop={"x": 0.9, "y": 0.9, "w": 0.5, "h": 0.5},
        )
        crop_w, crop_h, crop_x, crop_y = compute_crop_pixels(options)
        self.assertLessEqual(crop_x + crop_w, 1000)
        self.assertLessEqual(crop_y + crop_h, 1000)
        self.assertGreaterEqual(crop_x, 0)
        self.assertGreaterEqual(crop_y, 0)

    def test_crop_requires_source_dimensions(self) -> None:
        options = make_options(
            speed=1.0,
            source_width=0,
            source_height=0,
            crop={"x": 0, "y": 0, "w": 0.5, "h": 0.5},
        )
        with self.assertRaises(FFmpegBuildError):
            compute_crop_pixels(options)

    def test_pipeline_order_is_subtitles_concat_transform_crop_speed(self) -> None:
        options = make_options(
            speed=2.0,
            segments=[{"start": 1, "end": 3}, {"start": 5, "end": 7}],
            subtitle_path="/media/movie.srt",
            rotation="90_cw",
            flip_horizontal=True,
            crop={"x": 0.1, "y": 0.1, "w": 0.6, "h": 0.6},
        )
        graph = build_filter_graph(options)
        order = [
            graph.filter_complex.index("subtitles="),
            graph.filter_complex.index("trim=start=1"),
            graph.filter_complex.index("concat="),
            graph.filter_complex.index("transpose=1"),
            graph.filter_complex.index("crop="),
            graph.filter_complex.index("setpts=PTS/2"),
            graph.filter_complex.index("atempo=2"),
        ]
        self.assertEqual(order, sorted(order))
        self.assertEqual(graph.video_label, "v_speed")
        self.assertEqual(graph.audio_label, "a_speed")


class CommandTests(unittest.TestCase):
    def test_nvenc_h264_flags(self) -> None:
        command = build_ffmpeg_command(make_options(codec="h264"))
        joined = " ".join(command)
        self.assertIn("-c:v h264_nvenc", joined)
        self.assertIn("-preset p6", joined)
        self.assertIn("-rc vbr", joined)
        self.assertIn("-cq 19", joined)
        self.assertIn("-b:v 0", joined)
        self.assertIn("-fps_mode passthrough", joined)
        self.assertIn("-movflags +faststart", joined)

    def test_nvenc_hevc_flags_include_hvc1_tag(self) -> None:
        command = build_ffmpeg_command(make_options(codec="hevc"))
        joined = " ".join(command)
        self.assertIn("-c:v hevc_nvenc", joined)
        self.assertIn("-cq 21", joined)
        self.assertIn("-tag:v hvc1", joined)
        self.assertIn("-map [v_speed]", joined)
        self.assertIn("-map [a_speed]", joined)

    def test_cpu_fallback_encoders(self) -> None:
        command = build_ffmpeg_command(
            make_options(
                h264_encoder="libx264", hevc_encoder="libx265", ffmpeg_major=4
            )
        )
        joined = " ".join(command)
        self.assertIn("-c:v libx264", joined)
        self.assertIn("-crf 19", joined)
        self.assertIn("-vsync passthrough", joined)
        self.assertNotIn("-fps_mode", joined)

    def test_input_and_autorotate_ordering(self) -> None:
        command = build_ffmpeg_command(make_options())
        self.assertLess(command.index("-noautorotate"), command.index("-i"))
        self.assertEqual(Path(command[-1]), Path("/media/out.mp4"))
        self.assertIn("-progress", command)

    def test_multi_cut_maps_concat_pads(self) -> None:
        command = build_ffmpeg_command(
            make_options(
                speed=1.0,
                segments=[{"start": 1, "end": 2}, {"start": 3, "end": 4}],
            )
        )
        joined = " ".join(command)
        self.assertIn("-map [v_cat]", joined)
        self.assertIn("-map [a_cat]", joined)
        self.assertIn("-fps_mode passthrough", joined)

    def test_subtitle_only_render_maps_raw_audio_stream(self) -> None:
        command = build_ffmpeg_command(
            make_options(
                speed=1.0, subtitle_path="/media/captions.srt", subtitle_font_size=16
            )
        )
        joined = " ".join(command)
        self.assertIn("-map [v_sub]", joined)
        self.assertIn("-map 0:a:0", joined)
        self.assertIn("FontSize=16", joined)

    def test_speed_outside_range_is_rejected(self) -> None:
        with self.assertRaises(FFmpegBuildError):
            build_ffmpeg_command(make_options(speed=5.0))
        with self.assertRaises(FFmpegBuildError):
            build_ffmpeg_command(make_options(speed=0.1))

    def test_invalid_container_rejected(self) -> None:
        with self.assertRaises(FFmpegBuildError):
            build_ffmpeg_command(make_options(output_path="/media/out.avi"))


if __name__ == "__main__":
    unittest.main()
