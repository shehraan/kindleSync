package dev.kobosync;

import java.io.File;
import java.io.IOException;
import java.io.StringReader;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;

public final class CaptionTimelineTest {
    private static final String CUES = "WEBVTT\n\n"
            + "00:00:01.000 --> 00:00:02.500\nFirst cue\n\n"
            + "00:00:03.500 --> 00:00:04.500\nSecond cue\n\n"
            + "00:00:04.500 --> 00:00:05.000\nThird cue\n";
    private static final String ONE = "First gives us ten clear words for this caption window.";
    private static final String TWO = "Second gives us ten clear words for this caption window.";
    private static final String THREE = "Third gives us ten clear words for this caption window.";
    private static final String FOUR = "Fourth gives us ten clear words for this caption window.";
    private static final String FIVE = "Fifth gives us ten clear words for this caption window.";
    private static final String SIX = "Sixth gives us ten clear words for this caption window.";
    private static final String SEVEN = "Seventh gives us ten clear words for this caption window.";
    private static final String EIGHT = "Eighth gives us ten clear words for this caption window.";
    private static final String FIRST_WINDOW = ONE + " " + TWO + " " + THREE + " " + FOUR + " " + FIVE;
    private static final String SECOND_WINDOW = TWO + " " + THREE + " " + FOUR + " " + FIVE + " " + SIX;
    private static final String THIRD_WINDOW = THREE + " " + FOUR + " " + FIVE + " " + SIX + " " + SEVEN;
    private static final String LAST_WINDOW = FOUR + " " + FIVE + " " + SIX + " " + SEVEN + " " + EIGHT;
    private static final String CONTEXT_CUES = "WEBVTT\n\n"
            + "00:00:01.000 --> 00:00:02.000\n" + ONE + "\n\n"
            + "00:00:03.000 --> 00:00:04.000\n" + TWO + "\n\n"
            + "00:00:05.000 --> 00:00:06.000\n" + THREE + "\n\n"
            + "00:00:07.000 --> 00:00:08.000\n" + FOUR + "\n\n"
            + "00:00:09.000 --> 00:00:10.000\n" + FIVE + "\n\n"
            + "00:00:11.000 --> 00:00:12.000\n" + SIX + "\n\n"
            + "00:00:13.000 --> 00:00:14.000\n" + SEVEN + "\n\n"
            + "00:00:15.000 --> 00:00:16.000\n" + EIGHT + "\n";

    public static void main(String[] args) throws Exception {
        int failures = 0;
        failures += run("exact boundaries and gaps", CaptionTimelineTest::boundaries);
        failures += run("backward seeks and repeated positions", CaptionTimelineTest::randomSeeks);
        failures += run("UTF-8 file, identifiers, entities and multiline text", CaptionTimelineTest::utf8File);
        failures += run("long-hour timestamps", CaptionTimelineTest::longHours);
        failures += run("empty captions", CaptionTimelineTest::emptyCaptions);
        failures += run("reader ownership", CaptionTimelineTest::readerOwnership);
        failures += run("malformed captions", CaptionTimelineTest::malformedCaptions);
        failures += run("unsorted and overlapping cues", CaptionTimelineTest::invalidOrdering);
        failures += run("context windows shift near timeline edges", CaptionTimelineTest::windowBoundaries);
        failures += run("context remains visible through gaps", CaptionTimelineTest::windowGaps);
        failures += run("context supports backward and repeated seeks", CaptionTimelineTest::windowSeeks);
        failures += run("short context preserves all text and line breaks", CaptionTimelineTest::shortWindow);
        failures += run("sparse captions expand to fifty words", CaptionTimelineTest::sparseWindow);
        if (failures != 0) throw new AssertionError(failures + " caption test groups failed");
        System.out.println("CaptionTimeline tests passed: 13 groups");
    }

    private static void boundaries() throws Exception {
        CaptionTimeline timeline = CaptionTimeline.parse(new StringReader(CUES));
        long[] positions = {-1, 0, 999, 1000, 2499, 2500, 3499, 3500, 4499, 4500, 4999, 5000, Long.MAX_VALUE};
        String[] expected = {"", "", "", "First cue", "First cue", "", "", "Second cue", "Second cue",
                "Third cue", "Third cue", "", ""};
        for (int i = 0; i < positions.length; i++) {
            equal(expected[i], timeline.at(positions[i]), "position " + positions[i]);
        }
    }

    private static void randomSeeks() throws Exception {
        CaptionTimeline timeline = CaptionTimeline.parse(new StringReader(CUES));
        long[] positions = {4900, 1100, 4900, 4900, 5000, 2499, 3500, 1200, 3000, 4500, 1000};
        String[] expected = {"Third cue", "First cue", "Third cue", "Third cue", "", "First cue",
                "Second cue", "First cue", "", "Third cue", "First cue"};
        for (int i = 0; i < positions.length; i++) {
            equal(expected[i], timeline.at(positions[i]), "seek " + positions[i]);
        }
    }

    private static void utf8File() throws Exception {
        File file = File.createTempFile("kobosync-captions-", ".vtt");
        try {
            String vtt = "\uFEFFWEBVTT\r\n\r\nchapter-1\r\n"
                    + "00:00:00.100 --> 00:00:04.000\r\n"
                    + "Café &amp; 茶 &lt;tag&gt; &nbsp;\r\nDeuxième &amp;lt;\r\n\r\n";
            Files.write(file.toPath(), vtt.getBytes(StandardCharsets.UTF_8));
            CaptionTimeline timeline = CaptionTimeline.load(file);
            equal("Café & 茶 <tag> \u00a0\nDeuxième &lt;", timeline.at(100), "decoded file text");
            equal("", timeline.at(4000), "file cue end");
        } finally {
            Files.deleteIfExists(file.toPath());
        }
    }

    private static void longHours() throws Exception {
        CaptionTimeline timeline = CaptionTimeline.parse(new StringReader("WEBVTT\n\n"
                + "100:02:03.004 --> 100:02:04.005\nA long audiobook\n\n"
                + "2562047788015:12:55.806 --> 2562047788015:12:55.807\nLast millisecond\n"));
        equal("", timeline.at(360123003), "before long-hour cue");
        equal("A long audiobook", timeline.at(360123004), "long-hour start");
        equal("A long audiobook", timeline.at(360124004), "long-hour final millisecond");
        equal("", timeline.at(360124005), "long-hour end");
        equal("Last millisecond", timeline.at(Long.MAX_VALUE - 1), "largest supported time");
        equal("", timeline.at(Long.MAX_VALUE), "largest end is exclusive");
    }

    private static void emptyCaptions() throws Exception {
        for (String vtt : new String[] {"WEBVTT", "WEBVTT\n", "WEBVTT\n\n", "WEBVTT\n\n\n"}) {
            CaptionTimeline timeline = CaptionTimeline.parse(new StringReader(vtt));
            equal("", timeline.at(0), "empty at zero");
            equal("", timeline.at(123456), "empty after seek");
            equal("", timeline.windowAt(0), "empty context at zero");
            equal("", timeline.windowAt(Long.MAX_VALUE), "empty context after seek");
        }
    }

    private static void readerOwnership() throws Exception {
        TrackingReader valid = new TrackingReader(CUES);
        CaptionTimeline timeline = CaptionTimeline.parse(valid);
        equal("First cue", timeline.at(1000), "caller reader content");
        if (valid.closed) throw new AssertionError("parse closed the caller's reader");
        valid.ready();
        TrackingReader invalid = new TrackingReader("bad header");
        try {
            CaptionTimeline.parse(invalid);
            throw new AssertionError("invalid reader accepted");
        } catch (IOException expected) {
            if (invalid.closed) throw new AssertionError("parse closed the reader after failure");
            invalid.ready();
        }
    }

    private static void malformedCaptions() throws Exception {
        String[] invalid = {
            "", "not WEBVTT\n\n", "WEBVTT\n00:00:00.000 --> 00:00:01.000\ntext\n",
            "WEBVTT\n\nidentifier\nplain text\n", "WEBVTT\n\nidentifier\n\n",
            cue("-01:00:00.000", "00:00:01.000"),
            cue("00:60:00.000", "01:01:00.000"),
            cue("00:00:60.000", "00:01:01.000"),
            cue("00:00:00.00", "00:00:01.000"),
            cue("00:00:00.0000", "00:00:01.000"),
            cue("00:00:00,000", "00:00:01.000"),
            cue("00:00:00.000", "00:00:00.000"),
            cue("00:00:02.000", "00:00:01.000"),
            cue("2562047788016:00:00.000", "2562047788017:00:00.000"),
            cue("9223372036854775808:00:00.000", "9223372036854775809:00:00.000"),
            "WEBVTT\n\n00:00:00.000 -> 00:00:01.000\ntext\n",
            "WEBVTT\n\n00:00:00.000 --> 00:00:01.000 align:start\ntext\n",
            "WEBVTT\n\n00:00:00.000 --> 00:00:01.000\n",
            "WEBVTT\n\n00:00:00.000 --> 00:00:01.000\n\n",
            "WEBVTT\n\n00:00:00.000 --> 00:00:01.000\ntext\n00:00:02.000 --> 00:00:03.000\ntext\n"
        };
        for (int i = 0; i < invalid.length; i++) reject(invalid[i], "malformed fixture " + i);
    }

    private static void invalidOrdering() throws Exception {
        reject("WEBVTT\n\n00:00:01.000 --> 00:00:03.000\nfirst\n\n"
                + "00:00:02.000 --> 00:00:04.000\noverlap\n", "overlap");
        reject("WEBVTT\n\n00:00:03.000 --> 00:00:04.000\nfirst\n\n"
                + "00:00:01.000 --> 00:00:02.000\nunsorted\n", "unsorted");
        reject("WEBVTT\n\n00:00:01.000 --> 00:00:03.000\nfirst\n\n"
                + "00:00:01.000 --> 00:00:03.000\nduplicate\n", "duplicate interval");
    }

    private static void windowBoundaries() throws Exception {
        CaptionTimeline timeline = CaptionTimeline.parse(new StringReader(CONTEXT_CUES));
        equal(FIRST_WINDOW, timeline.windowAt(Long.MIN_VALUE), "before first cue");
        equal(FIRST_WINDOW, timeline.windowAt(1000), "first cue");
        equal(FIRST_WINDOW, timeline.windowAt(3000), "second cue");
        equal(FIRST_WINDOW, timeline.windowAt(4999), "before third cue");
        equal(SECOND_WINDOW, timeline.windowAt(5000), "previous, current and next three");
        equal(THIRD_WINDOW, timeline.windowAt(7000), "middle window");
        equal(LAST_WINDOW, timeline.windowAt(9000), "final full window");
        equal(LAST_WINDOW, timeline.windowAt(15000), "last cue still has five cues");
        equal(LAST_WINDOW, timeline.windowAt(Long.MAX_VALUE), "after last cue");
    }

    private static void windowGaps() throws Exception {
        CaptionTimeline timeline = CaptionTimeline.parse(new StringReader(CONTEXT_CUES));
        equal("", timeline.at(0), "strict cue before audio begins");
        equal(FIRST_WINDOW, timeline.windowAt(0), "context before audio begins");
        equal(THREE, timeline.at(5999), "strict final cue millisecond");
        equal(SECOND_WINDOW, timeline.windowAt(5999), "context before gap");
        equal("", timeline.at(6000), "strict gap start");
        equal(SECOND_WINDOW, timeline.windowAt(6000), "context holds at gap start");
        equal(SECOND_WINDOW, timeline.windowAt(6999), "context holds through gap");
        equal(FOUR, timeline.at(7000), "strict next cue start");
        equal(THIRD_WINDOW, timeline.windowAt(7000), "context moves at next cue start");
        equal("", timeline.at(16000), "strict captions end");
        equal(LAST_WINDOW, timeline.windowAt(16000), "last context remains visible");
    }

    private static void windowSeeks() throws Exception {
        CaptionTimeline timeline = CaptionTimeline.parse(new StringReader(CONTEXT_CUES));
        long[] positions = {14999, 5000, 7000, 0, 6500, 6500, 16000, 3500, Long.MIN_VALUE};
        String[] expected = {LAST_WINDOW, SECOND_WINDOW, THIRD_WINDOW, FIRST_WINDOW,
                SECOND_WINDOW, SECOND_WINDOW, LAST_WINDOW, FIRST_WINDOW, FIRST_WINDOW};
        for (int i = 0; i < positions.length; i++) {
            equal(expected[i], timeline.windowAt(positions[i]), "context seek " + positions[i]);
        }
    }

    private static void shortWindow() throws Exception {
        CaptionTimeline three = CaptionTimeline.parse(new StringReader(CUES));
        equal("First cue Second cue Third cue", three.windowAt(0), "all available cues before first");
        equal("First cue Second cue Third cue", three.windowAt(3500), "all available cues during playback");
        equal("First cue Second cue Third cue", three.windowAt(99999), "all available cues after last");
        CaptionTimeline one = CaptionTimeline.parse(new StringReader("WEBVTT\n\n"
                + "00:00:01.000 --> 00:00:02.000\nCafé &amp; tea.\nSecond line.\n"));
        equal("Café & tea.\nSecond line.", one.windowAt(0), "single multiline cue before first");
        equal("Café & tea.\nSecond line.", one.windowAt(2000), "single multiline cue after last");
    }

    private static void sparseWindow() throws Exception {
        StringBuilder vtt = new StringBuilder("WEBVTT\n\n");
        for (int i = 1; i <= 18; i++) {
            vtt.append(String.format(java.util.Locale.ROOT,
                    "00:00:%02d.000 --> 00:00:%02d.000\nLine%02d has four words.\n\n", i * 2, i * 2 + 1, i));
        }
        CaptionTimeline timeline = CaptionTimeline.parse(new StringReader(vtt.toString()));
        String first = "Line01 has four words. Line02 has four words. Line03 has four words. "
                + "Line04 has four words. Line05 has four words. Line06 has four words. "
                + "Line07 has four words. Line08 has four words. Line09 has four words. "
                + "Line10 has four words. Line11 has four words. Line12 has four words. Line13 has four words.";
        String middle = "Line04 has four words. Line05 has four words. Line06 has four words. "
                + "Line07 has four words. Line08 has four words. Line09 has four words. "
                + "Line10 has four words. Line11 has four words. Line12 has four words. "
                + "Line13 has four words. Line14 has four words. Line15 has four words. Line16 has four words.";
        String last = "Line06 has four words. Line07 has four words. Line08 has four words. "
                + "Line09 has four words. Line10 has four words. Line11 has four words. "
                + "Line12 has four words. Line13 has four words. Line14 has four words. "
                + "Line15 has four words. Line16 has four words. Line17 has four words. Line18 has four words.";
        equal(first, timeline.windowAt(0), "sparse first window has 52 words");
        equal(middle, timeline.windowAt(10000), "sparse window starts one cue before current");
        equal("", timeline.at(11000), "sparse strict gap remains blank");
        equal(middle, timeline.windowAt(11000), "sparse context holds through gap");
        equal(last, timeline.windowAt(36000), "sparse last window backfills to 52 words");
        equal(last, timeline.windowAt(Long.MAX_VALUE), "sparse context remains after audio");
        equal(first, timeline.windowAt(0), "sparse context supports backwards seek");
    }

    private static String cue(String start, String end) {
        return "WEBVTT\n\n" + start + " --> " + end + "\ntext\n";
    }

    private static void reject(String input, String context) throws Exception {
        try {
            CaptionTimeline.parse(new StringReader(input));
        } catch (IOException expected) {
            return;
        }
        throw new AssertionError("accepted " + context);
    }

    private static void equal(String expected, String actual, String context) {
        if (!expected.equals(actual)) {
            throw new AssertionError(context + ": expected <" + expected + "> but was <" + actual + ">");
        }
    }

    private static int run(String name, CheckedTest test) {
        try {
            test.run();
            return 0;
        } catch (Exception | AssertionError failure) {
            System.err.println("FAIL " + name + ": " + failure);
            return 1;
        }
    }

    private interface CheckedTest {
        void run() throws Exception;
    }

    private static final class TrackingReader extends StringReader {
        boolean closed;

        TrackingReader(String value) { super(value); }

        @Override public void close() {
            closed = true;
            super.close();
        }
    }
}
