package dev.kobosync;

import java.io.BufferedReader;
import java.io.File;
import java.io.FileInputStream;
import java.io.IOException;
import java.io.InputStreamReader;
import java.io.Reader;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.List;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/** Immutable, non-overlapping plain-text WebVTT cues for random playback seeks. */
public final class CaptionTimeline {
    private static final Pattern TIMING = Pattern.compile(
            "([0-9]{2,}:[0-9]{2}:[0-9]{2}\\.[0-9]{3})[ \\t]+-->[ \\t]+"
            + "([0-9]{2,}:[0-9]{2}:[0-9]{2}\\.[0-9]{3})");
    private static final Pattern TIMESTAMP = Pattern.compile(
            "([0-9]{2,}):([0-9]{2}):([0-9]{2})\\.([0-9]{3})");

    private final long[] starts;
    private final long[] ends;
    private final long[] wordOffsets;
    private final String[] texts;

    private CaptionTimeline(List<Cue> cues) {
        starts = new long[cues.size()];
        ends = new long[cues.size()];
        wordOffsets = new long[cues.size() + 1];
        texts = new String[cues.size()];
        for (int i = 0; i < cues.size(); i++) {
            Cue cue = cues.get(i);
            starts[i] = cue.start;
            ends[i] = cue.end;
            texts[i] = cue.text;
            wordOffsets[i + 1] = wordOffsets[i] + wordCount(cue.text);
        }
    }

    /** Loads UTF-8 captions and closes the file on success or failure. */
    public static CaptionTimeline load(File file) throws IOException {
        try (Reader reader = new InputStreamReader(new FileInputStream(file),
                StandardCharsets.UTF_8.newDecoder())) {
            return parse(reader);
        }
    }

    /** Parses basic WebVTT without closing the caller's reader. */
    public static CaptionTimeline parse(Reader reader) throws IOException {
        BufferedReader input = reader instanceof BufferedReader
                ? (BufferedReader) reader : new BufferedReader(reader);
        String line = input.readLine();
        if (line != null && line.startsWith("\uFEFF")) line = line.substring(1);
        if (!"WEBVTT".equals(line)) throw new IOException("Missing WEBVTT header");

        List<Cue> cues = new ArrayList<>();
        line = input.readLine();
        if (line != null && !line.trim().isEmpty()) {
            throw new IOException("Expected blank line after WEBVTT header");
        }
        while ((line = input.readLine()) != null) {
            if (line.trim().isEmpty()) continue;
            // An identifier may precede the timing line, but cannot contain an arrow.
            if (!line.contains("-->")) line = input.readLine();
            Matcher timing = line == null ? null : TIMING.matcher(line);
            if (timing == null || !timing.matches()) {
                throw new IOException("Expected WebVTT cue timing");
            }
            long start = timestamp(timing.group(1));
            long end = timestamp(timing.group(2));
            if (end <= start) throw new IOException("Caption end must follow its start");
            if (!cues.isEmpty() && start < cues.get(cues.size() - 1).end) {
                throw new IOException("Captions must be ordered and cannot overlap");
            }

            StringBuilder text = new StringBuilder();
            while ((line = input.readLine()) != null && !line.trim().isEmpty()) {
                if (line.contains("-->")) {
                    throw new IOException("Unexpected cue timing inside caption text");
                }
                if (text.length() > 0) text.append('\n');
                text.append(line);
            }
            if (text.length() == 0) throw new IOException("Missing caption text");
            cues.add(new Cue(start, end, decode(text.toString())));
        }
        return new CaptionTimeline(cues);
    }

    /** Returns the cue at start <= position < end, or an empty string outside cues. */
    public String at(long positionMs) {
        int cue = cueAtOrBefore(positionMs);
        return cue >= 0 && positionMs < ends[cue] ? texts[cue] : "";
    }

    /**
     * Returns at least five cues and fifty words where available, starting one cue before current.
     * Extends forward, backfills at the end, holds through gaps, and preserves line breaks.
     */
    public String windowAt(long positionMs) {
        if (texts.length == 0) return "";
        int anchor = Math.max(0, cueAtOrBefore(positionMs));
        int first = Math.max(0, anchor - 1);
        int low = first + Math.min(5, texts.length - first);
        int high = texts.length;
        long target = wordOffsets[first] + 50;
        while (low < high) {
            int middle = low + (high - low) / 2;
            if (wordOffsets[middle] < target) low = middle + 1;
            else high = middle;
        }
        int end = low;
        first = Math.min(first, Math.max(0, end - 5));
        if (wordOffsets[end] - wordOffsets[first] < 50) {
            target = wordOffsets[end] - 50;
            low = 0;
            high = first + 1;
            while (low < high) {
                int middle = low + (high - low) / 2;
                if (wordOffsets[middle] <= target) low = middle + 1;
                else high = middle;
            }
            first = Math.max(0, low - 1);
        }
        StringBuilder text = new StringBuilder();
        for (int i = first; i < end; i++) {
            if (i > first) text.append(' ');
            text.append(texts[i]);
        }
        return text.toString();
    }

    private int cueAtOrBefore(long positionMs) {
        int low = 0;
        int high = starts.length;
        while (low < high) {
            int middle = low + (high - low) / 2;
            if (starts[middle] <= positionMs) low = middle + 1;
            else high = middle;
        }
        return low - 1;
    }

    private static long wordCount(String text) {
        long count = 0;
        boolean inWord = false;
        for (int i = 0; i < text.length(); i++) {
            char value = text.charAt(i);
            if (Character.isWhitespace(value) || Character.isSpaceChar(value)) inWord = false;
            else if (!inWord) {
                count++;
                inWord = true;
            }
        }
        return count;
    }

    private static long timestamp(String value) throws IOException {
        Matcher parts = TIMESTAMP.matcher(value);
        if (!parts.matches()) throw new IOException("Invalid caption timestamp: " + value);
        try {
            long hours = Long.parseLong(parts.group(1));
            int minutes = Integer.parseInt(parts.group(2));
            int seconds = Integer.parseInt(parts.group(3));
            int millis = Integer.parseInt(parts.group(4));
            if (minutes >= 60 || seconds >= 60) {
                throw new IOException("Invalid caption timestamp: " + value);
            }
            return Math.addExact(Math.multiplyExact(hours, 3600000L),
                    minutes * 60000L + seconds * 1000L + millis);
        } catch (NumberFormatException | ArithmeticException invalid) {
            throw new IOException("Caption timestamp is too large: " + value, invalid);
        }
    }

    private static String decode(String value) {
        // Decode ampersands last so escaped entity text is decoded only once.
        return value.replace("&nbsp;", "\u00a0").replace("&lt;", "<")
                .replace("&gt;", ">").replace("&amp;", "&");
    }

    private static final class Cue {
        final long start;
        final long end;
        final String text;

        Cue(long start, long end, String text) {
            this.start = start;
            this.end = end;
            this.text = text;
        }
    }
}
