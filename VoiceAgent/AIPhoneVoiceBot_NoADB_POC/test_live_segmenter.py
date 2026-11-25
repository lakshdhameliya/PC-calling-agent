import collections
import unittest
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import Mock

import numpy as np

from phone_link_no_adb_bot import (
    HINDI_REPEAT_PROMPT,
    JobQueue,
    LiveFaqSegmenter,
    FrontbitLinkNoAdbBot,
    bot_echo_score,
    collapse_repeated_sentences,
    strip_prompt_leak,
)
import phone_link_no_adb_bot as bot_module
import bestzone_faq

_CATALOG = bestzone_faq.load_catalog()
WALL_HOOK_PRICE = _CATALOG["answers"][f"{_CATALOG['products'].index('wall hook')}|0"]
PRICE_GENERAL = _CATALOG["templates"][0]
from unittest.mock import patch


class LiveFaqSegmenterTests(unittest.TestCase):
    def test_emits_preview_then_final_after_700ms_silence(self):
        emitted = []
        started = []
        segmenter = LiveFaqSegmenter(
            lambda audio, at, utterance_id, final: emitted.append((len(audio), utterance_id, final)),
            lambda utterance_id, at: started.append(utterance_id),
            silence_frames=7,
        )
        speech = np.full(1600, 0.05, dtype=np.float32)
        silence = np.zeros(1600, dtype=np.float32)

        for _ in range(8):
            segmenter.feed(speech)
        self.assertTrue(any(not final for _, _, final in emitted))
        self.assertFalse(any(final for _, _, final in emitted))

        for _ in range(7):
            segmenter.feed(silence)
        self.assertEqual(started, [1])
        self.assertTrue(emitted[-1][2])
        self.assertTrue(all(utterance_id == 1 for _, utterance_id, _ in emitted))

    def test_keeps_a_short_pause_inside_one_question(self):
        finals = []
        segmenter = LiveFaqSegmenter(
            lambda audio, at, utterance_id, final: finals.append((len(audio), utterance_id)) if final else None,
            lambda *args: None,
            silence_frames=7,
        )
        speech = np.full(1600, 0.05, dtype=np.float32)
        silence = np.zeros(1600, dtype=np.float32)
        for _ in range(5):
            segmenter.feed(speech)
        for _ in range(4):
            segmenter.feed(silence)
        for _ in range(5):
            segmenter.feed(speech)
        for _ in range(7):
            segmenter.feed(silence)
        self.assertEqual(len(finals), 1)
        self.assertEqual(finals[0][1], 1)
        self.assertGreaterEqual(finals[0][0] / 1600, 5 + 4 + 5)

    def test_question_ends_despite_steady_background_noise(self):
        # Call log 2026-10-30 19:46: background noise kept questions open to the cap.
        finals = []
        segmenter = LiveFaqSegmenter(
            lambda audio, at, uid, final: finals.append(len(audio)) if final else None,
            lambda *args: None, threshold=0.003, silence_frames=5, max_frames=50, end_ratio=0.2)
        speech = np.full(1600, 0.10, dtype=np.float32)
        noise = np.full(1600, 0.015, dtype=np.float32)
        for _ in range(4):
            segmenter.feed(noise)  # noise before the caller talks sets the floor
        for _ in range(15):
            segmenter.feed(speech)
        for _ in range(5):
            segmenter.feed(noise)
        self.assertEqual(len(finals), 1)
        self.assertLessEqual(finals[0] / 16000.0, 2.6)

    def test_silence_does_not_start_or_emit(self):
        emitted = []
        started = []
        segmenter = LiveFaqSegmenter(
            lambda *args: emitted.append(args),
            lambda *args: started.append(args),
        )
        for _ in range(20):
            segmenter.feed(np.zeros(1600, dtype=np.float32))
        segmenter.flush()
        self.assertEqual(started, [])
        self.assertEqual(emitted, [])


    def test_speculative_preview_on_first_silent_frame(self):
        emitted = []
        segmenter = LiveFaqSegmenter(
            lambda audio, at, utterance_id, final: emitted.append((len(audio), final, segmenter.voiced_frames)),
            lambda *args: None,
            silence_frames=3,
        )
        speech = np.full(1600, 0.05, dtype=np.float32)
        silence = np.zeros(1600, dtype=np.float32)
        for _ in range(4):
            segmenter.feed(speech)
        segmenter.feed(silence)
        self.assertEqual(emitted[-1][1:], (False, 4))
        segmenter.feed(silence)
        segmenter.feed(silence)
        # Final carries the same voiced-frame count, so its transcript can be reused.
        self.assertEqual(emitted[-1][1:], (True, 4))
        self.assertIsNotNone(segmenter.last_voiced_at)


class JobQueueTests(unittest.TestCase):
    def job(self, utterance_id, final):
        return {"utterance_id": utterance_id, "final": final}

    def test_final_beats_preview_and_drops_it(self):
        jobs = JobQueue()
        jobs.put(self.job(1, False))
        self.assertEqual(jobs.put(self.job(1, True)), 1)
        self.assertEqual(jobs.get(), self.job(1, True))
        self.assertEqual(jobs.qsize(), 0)

    def test_preview_never_displaces_a_final(self):
        jobs = JobQueue()
        jobs.put(self.job(1, True))
        jobs.put(self.job(2, False))
        self.assertEqual(jobs.get(), self.job(1, True))
        self.assertEqual(jobs.qsize(), 0)

    def test_newest_final_replaces_waiting_finals(self):
        # Live call 2026-10-27: repeated questions piled up to 18 s of backlog.
        jobs = JobQueue()
        jobs.put(self.job(1, True))
        jobs.put(self.job(2, True))
        self.assertEqual(jobs.put(self.job(3, True)), 1)
        self.assertEqual(jobs.get(), self.job(3, True))
        self.assertEqual(jobs.qsize(), 0)

    def test_only_newest_preview_is_kept(self):
        jobs = JobQueue()
        jobs.put(self.job(1, False))
        jobs.put(self.job(2, False))
        self.assertEqual(jobs.get(), self.job(2, False))


class CollapseRepeatsTests(unittest.TestCase):
    def test_back_to_back_repeats_are_removed(self):
        self.assertEqual(collapse_repeated_sentences("अपका नाम क्या है? अपका नाम क्या है?"), "अपका नाम क्या है?")
        self.assertEqual(collapse_repeated_sentences("एक मिनिस रुके एक मिनिस रुके"), "एक मिनिस रुके")
        self.assertEqual(
            collapse_repeated_sentences("इस में कितने बैसे अलगते है? इस में कितने बैसे अलगते हैं?"),
            "इस में कितने बैसे अलगते है?",
        )

    def test_different_sentences_are_kept(self):
        text = "आपका नाम क्या है? अपिस का समय क्या है?"
        self.assertEqual(collapse_repeated_sentences(text), text)
        self.assertEqual(collapse_repeated_sentences("कीमत क्या है"), "कीमत क्या है")

    def test_cut_off_repeat_is_removed(self):
        # Live call 2026-10-31: Whisper restarted the sentence and stopped mid-word.
        self.assertEqual(collapse_repeated_sentences("मेरा देटा सूलोखीजे मेरा देटा सूलोखीज"), "मेरा देटा सूलोखीजे")
        self.assertEqual(collapse_repeated_sentences("बेश्धों क्यार बेश"), "बेश्धों क्यार")
        self.assertEqual(collapse_repeated_sentences("उर्सेल में दिस्काूंत मिलेगा उर्सेल में दिस्का�"),
                         "उर्सेल में दिस्काूंत मिलेगा")
        for text in ("क्या आप मैन्युफैक्चरर हो क्या", "मैं मैंने ऑर्डर किया", "मेरा ऑर्डर कहाँ है"):
            self.assertEqual(collapse_repeated_sentences(text), text)


class PromptLeakTests(unittest.TestCase):
    def test_trailing_prompt_sentence_is_removed(self):
        # Live call 2026-10-28: caller asked "कैसे हो", bot answered appointment.
        prompt = "नमस्ते। आप कैसे हैं? कीमत क्या है? अपॉइंटमेंट बुक करना है। धन्यवाद।"
        with patch.object(bot_module, "HINDI_INITIAL_PROMPT", prompt):
            self.assertEqual(strip_prompt_leak("के से हों? अपॉइंटमेंट बुक करना है?"), "के से हों?")
            self.assertEqual(strip_prompt_leak("आपका नाम? कीमत क्या है।"), "आपका नाम?")
        # A real follow-up that only resembles a prompt sentence is kept.
        with patch.object(bot_module, "HINDI_INITIAL_PROMPT", "आपका नाम क्या है?"):
            text = "आपका अपिस का है? आपका पता क्या है?"
            self.assertEqual(strip_prompt_leak(text), text)

    def test_single_sentence_and_real_second_question_are_kept(self):
        prompt = "नमस्ते। आप कैसे हैं? कीमत क्या है? धन्यवाद।"
        with patch.object(bot_module, "HINDI_INITIAL_PROMPT", prompt):
            self.assertEqual(strip_prompt_leak("अपॉइंटमेंट बुक करना है।"), "अपॉइंटमेंट बुक करना है।")
            text = "हैलो। मुझे अपॉइंटमेंट बुक करना है।"
            self.assertEqual(strip_prompt_leak(text), text)


class EchoTests(unittest.TestCase):
    def test_bot_answers_heard_back_are_echo(self):
        # Real transcripts from the 2026-10-24 call log.
        cases = (
            ("मैं आप का सवाल समच नहीं बाया।", HINDI_REPEAT_PROMPT),
            ("मैं आप का लोकल वोईश असिस्टिन्त हूँ।", "मैं आपका लोकल वॉइस असिस्टेंट हूँ।"),
            ("क्रिप्या एक बार मेरे पास अभी इस सवाल का जवाब नहीं हैं", "मेरे पास अभी इस सवाल का जवाब नहीं है।"),
            # Live call 2026-10-27 (these were answered with "please repeat").
            ("क्रिप्या अपनी पसन्ग की तारीक और समय बताये।", "हाँ। कृपया अपनी पसंद की तारीख और समय बताइए।"),
            ("प्या अपनी पसन की तारीख और समय बतायें", "हाँ। कृपया अपनी पसंद की तारीख और समय बताइए।"),
            ("त्रिप्या अपनी पसन्त की तारीक और समय बताये।", "हाँ। कृपया अपनी पसंद की तारीख और समय बताइए।"),
        )
        for heard, spoken in cases:
            with self.subTest(heard=heard):
                self.assertGreaterEqual(bot_echo_score(heard, spoken), 0.60)

    def test_real_questions_are_not_echo(self):
        cases = (
            ("आप क्या कर सकते हैं", "आप इसी फोन नंबर पर हमसे संपर्क कर सकते हैं।"),
            ("आपका नाम क्या है", "नमस्ते! मैं आपकी कैसे मदद कर सकता हूँ?"),
            ("अपॉइंटमेंट बुक करना है", "हम सोमवार से शनिवार, सुबह नौ बजे से शाम छह बजे तक उपलब्ध हैं।"),
            ("कीमत क्या है", "हाँ। कृपया अपनी पसंद की तारीख और समय बताइए।"),
        )
        for heard, spoken in cases:
            with self.subTest(heard=heard):
                self.assertLess(bot_echo_score(heard, spoken), 0.60)


class LiveBotTests(unittest.TestCase):
    def bare_bot(self):
        import collections
        import threading

        bot = FrontbitLinkNoAdbBot.__new__(FrontbitLinkNoAdbBot)
        bot.voice = Mock()
        bot.voice.pcm_cache = {}
        bot.voice_ready = threading.Event()
        bot.voice_ready.set()
        bot.voice_active = threading.Event()
        bot.set_status = Mock()
        bot.log = Mock()
        bot.jobs = JobQueue()
        bot.answered_utterances = set()
        bot.last_question = None
        bot.last_question_at = 0.0
        bot.voice_requested_at = None
        bot.call_active = True
        bot.call_detection_source = "hands_free_session"
        bot.fallback_trigger_utterance_id = None
        bot.welcome_guard_until = 0.0
        bot.last_call_activity_at = 0.0
        bot.preferred_language = "hi"
        bot.awaiting_language = False
        bot.pending_hindi_question = None
        bot.pending_hindi_question_at = 0.0
        bot.hindi_preview_candidates = {}
        bot.preview_transcripts = {}
        bot.recent_bot_speech = collections.deque(maxlen=6)
        bot.bot_speech_intervals = collections.deque(maxlen=8)
        bot.last_answer_text = None
        bot.last_answer_at = 0.0
        bot.last_bot_audio_end = None
        bot.pending_timing = None
        bot.stt_seconds_ema = None
        bot.hindi_stt = object()
        return bot

    def test_speech_start_marks_barge_in_without_cutting_answer(self):
        # Speech start alone must not stop the bot: echo/noise would cut answers.
        bot = self.bare_bot()
        bot.voice_active.set()
        bot.on_speech_start(2, datetime.now())
        bot.voice.send.assert_not_called()
        self.assertTrue(bot.set_status.call_args.kwargs["barge_in"])

    def test_no_answer_until_caller_finished(self):
        # A live preview never answers; only the final (full) question does.
        bot = self.bare_bot()
        bot.transcribe_hindi = Mock(return_value=("आपका नाम क्या है", 0.2))
        audio = np.full(16000, 0.05, dtype=np.float32)
        bot.process_audio(audio, datetime.now(), utterance_id=7, final=False, voiced_frames=9)
        bot.process_audio(audio, datetime.now(), utterance_id=7, final=False, voiced_frames=10)
        bot.voice.send.assert_not_called()
        bot.process_audio(audio, datetime.now(), utterance_id=7, final=True, voiced_frames=10)
        bot.voice.send.assert_any_call("speak", text="मैं आपका लोकल वॉइस असिस्टेंट हूँ।", language="hi")

    def test_far_quiet_voice_is_ignored(self):
        bot = self.bare_bot()
        bot.caller_levels = collections.deque([0.06, 0.05, 0.07])
        bot.transcribe_hindi = Mock(return_value=("वॉल हुक की कीमत क्या है", 0.2))
        bot.process_audio(np.full(32000, 0.012, dtype=np.float32), datetime.now(), 40, True)
        bot.voice.send.assert_not_called()
        self.assertEqual(bot.set_status.call_args.kwargs["skipped"], "far_voice_rejected")

    def test_final_reuses_preview_transcript_of_same_speech(self):
        bot = self.bare_bot()
        bot.transcribe_hindi = Mock(return_value=("वॉल हुक की कीमत क्या है", 0.4))
        audio = np.full(24000, 0.05, dtype=np.float32)
        bot.process_audio(audio, datetime.now(), 9, False, voiced_frames=15)
        bot.process_audio(audio, datetime.now(), 9, True, voiced_frames=15)
        self.assertEqual(bot.transcribe_hindi.call_count, 1)
        self.assertIn(9, bot.answered_utterances)
        self.assertTrue(bot.pending_timing["stt_reused_preview"])

    def test_final_with_more_speech_runs_stt_again(self):
        bot = self.bare_bot()
        bot.transcribe_hindi = Mock(return_value=("मुझे आपके", 0.4))
        audio = np.full(24000, 0.05, dtype=np.float32)
        bot.process_audio(audio, datetime.now(), 9, False, voiced_frames=10)
        bot.process_audio(audio, datetime.now(), 9, True, voiced_frames=15)
        fast_calls = [c for c in bot.transcribe_hindi.call_args_list if not c.kwargs.get("full_window")]
        self.assertEqual(len(fast_calls), 2)

    def test_unmatched_fast_transcript_is_rechecked_with_full_window(self):
        bot = self.bare_bot()

        def fake(audio, preview=False, full_window=False):
            return ("वॉल हुक की कीमत क्या है" if full_window
                    else "वाल का क्या है भाई बोलो", 0.5)

        bot.transcribe_hindi = Mock(side_effect=fake)
        with patch.object(bot_module, "FULL_WINDOW_RECHECK", True):
            bot.process_audio(np.full(48000, 0.05, dtype=np.float32), datetime.now(), 12, True)
        self.assertTrue(any(c.kwargs.get("full_window") for c in bot.transcribe_hindi.call_args_list))
        bot.voice.send.assert_any_call("speak", text=WALL_HOOK_PRICE, language="hi")

    def test_garbled_bestzone_question_is_answered(self):
        # Real phone STT output for "वॉल हुक की कीमत क्या है".
        bot = self.bare_bot()
        bot.transcribe_hindi = Mock(return_value=("वाल हुक्की की मत क्या है?", 0.5))
        bot.process_audio(np.full(32000, 0.05, dtype=np.float32), datetime.now(), 30, True)
        bot.voice.send.assert_any_call("speak", text=WALL_HOOK_PRICE, language="hi")

    def test_product_is_remembered_for_follow_up(self):
        bot = self.bare_bot()
        audio = np.full(32000, 0.05, dtype=np.float32)
        bot.transcribe_hindi = Mock(return_value=("मुझे लंच बॉक्स चाहिए", 0.5))
        bot.process_audio(audio, datetime.now(), 31, True)
        bot.transcribe_hindi = Mock(return_value=("उसकी डिलीवरी कब तक होगी", 0.5))
        bot.process_audio(audio, datetime.now(), 32, True)
        lunch = _CATALOG["products"].index("lunch box")
        bot.voice.send.assert_any_call("speak", text=_CATALOG["answers"][f"{lunch}|15"], language="hi")

    def test_general_question_uses_website_faq_and_product_question_uses_csv(self):
        import hindi_faq
        bot = self.bare_bot()
        refund = next(a for q, a in hindi_faq.HINDI_FAQS if q[0] == "रिटर्न कैसे करें")
        self.assertEqual(bot.match_question("रिफंड कैसे मिलेगा")[0], refund)
        self.assertEqual(bot.match_question("वॉल हुक की कीमत क्या है")[0], WALL_HOOK_PRICE)

    def test_old_test_faq_answers_are_not_used(self):
        bot = self.bare_bot()
        bot.transcribe_hindi = Mock(return_value=("आप कहाँ स्थित हैं", 0.5))
        bot.process_audio(np.full(32000, 0.05, dtype=np.float32), datetime.now(), 33, True)
        spoken = [c.kwargs.get("text") for c in bot.voice.send.call_args_list if c.args[:1] == ("speak",)]
        self.assertNotIn("हमारा टेस्ट स्थान अहमदाबाद, गुजरात है।", spoken)

    def test_low_score_match_asks_to_repeat_then_uses_combined_text(self):
        bot = self.bare_bot()
        bot.transcribe_hindi = Mock(return_value=("इमत कितनी है बोलो जरा", 0.5))
        audio = np.full(32000, 0.05, dtype=np.float32)
        bot.process_audio(audio, datetime.now(), 14, True)
        bot.voice.send.assert_any_call("speak", text=HINDI_REPEAT_PROMPT, language="hi")
        bot.transcribe_hindi = Mock(return_value=("कीमत बताओ", 0.5))
        bot.process_audio(audio, datetime.now(), 15, True)
        bot.voice.send.assert_any_call("speak", text=PRICE_GENERAL, language="hi")

    def test_matched_fast_transcript_skips_full_window(self):
        bot = self.bare_bot()
        bot.transcribe_hindi = Mock(return_value=("कीमत क्या है", 0.5))
        bot.process_audio(np.full(32000, 0.05, dtype=np.float32), datetime.now(), 13, True)
        self.assertEqual(bot.transcribe_hindi.call_count, 1)

    def test_unclear_audio_during_bot_speech_gets_no_repeat_prompt(self):
        bot = self.bare_bot()
        now = datetime.now()
        bot.bot_speech_intervals.append([now, None])
        bot.transcribe_hindi = Mock(return_value=("मास की जी कुछ और बात", 0.3))
        bot.process_audio(np.full(32000, 0.05, dtype=np.float32), now, 21, True)
        bot.voice.send.assert_not_called()
        self.assertEqual(bot.set_status.call_args.kwargs["skipped"], "during_bot_speech")

    def test_audio_during_bot_speech_is_not_answered_or_transcribed(self):
        # Half-duplex: the bot's own long answer comes back through the call
        # (live call 2026-10-30: its capacity answer was answered again).
        bot = self.bare_bot()
        now = datetime.now()
        bot.bot_speech_intervals.append([now, None])
        bot.transcribe_hindi = Mock(return_value=("कीमत क्या है", 0.3))
        bot.process_audio(np.full(32000, 0.05, dtype=np.float32), now, 22, True)
        bot.voice.send.assert_not_called()
        bot.transcribe_hindi.assert_not_called()

    def test_loud_caller_during_bot_speech_is_heard(self):
        # Call log 2026-10-30: echo rms 0.004-0.045, caller 0.06-0.11.
        bot = self.bare_bot()
        now = datetime.now()
        bot.bot_speech_intervals.append([now, None])
        bot.transcribe_hindi = Mock(return_value=("वॉल हुक की कीमत क्या है", 0.3))
        bot.process_audio(np.full(32000, 0.09, dtype=np.float32), now, 41, True)
        bot.voice.send.assert_any_call("speak", text=WALL_HOOK_PRICE, language="hi")

    def test_stt_spelling_of_order_is_fixed(self):
        import hindi_faq
        bot = self.bare_bot()
        track = next(a for q, a in hindi_faq.HINDI_FAQS if q[0] == "ऑर्डर ट्रैक कैसे करें")
        self.assertEqual(bot.match_question("मूल सेल मेरा उड़र काहा है")[0], track)

    def test_garbled_start_and_word_order_still_understood(self):
        # Real transcripts from the 2026-10-30 calls.
        import hindi_faq
        first = {a: q[0] for q, a in hindi_faq.HINDI_FAQS}
        bot = self.bare_bot()
        cases = {
            "मुझे अवडर केमसल करना है.": "ऑर्डर कैंसिल कैसे करें",
            "शोडर केंषिल कना है": "ऑर्डर कैंसिल कैसे करें",
            "देख्रों पोग्राम क्याजे देख्रों पोग्राम क्": "रेफरल प्रोग्राम क्या है",
            "रिटन प्रोड़ा कोंचे नहीं होतें": "कौन से प्रोडक्ट रिटर्न नहीं होते",
            "विस्ट जों क्या है": "बेस्टज़ोन क्या है",
        }
        for heard, want in cases.items():
            with self.subTest(heard=heard):
                self.assertEqual(first.get(bot.match_question(heard)[0]), want)

    def test_every_dashboard_faq_question_reaches_its_own_answer(self):
        # Two FAQs sharing a question form would make the bot pick the wrong one.
        import hindi_faq
        bot = self.bare_bot()
        wrong = []
        for aliases, answer in hindi_faq.HINDI_FAQS:
            for alias in aliases:
                if alias.isascii():
                    continue
                bot.bestzone_product = None
                got, score, how = bot.match_question(alias)
                if got != answer or score < bot_module.live_gate(how):
                    wrong.append((alias, aliases[0], how))
        self.assertEqual(wrong, [])

    def test_heard_forms_from_live_call_reach_their_answer(self):
        # Heard on the 31-10 test call: spelled letters and a split word.
        import hindi_faq
        first = {a: q[0] for q, a in hindi_faq.HINDI_FAQS}
        bot = self.bare_bot()
        for heard, want in (("सी ओ डी है क्या", "कैश ऑन डिलीवरी है क्या"),
                            ("कैंस सिल करना", "ऑर्डर कैंसिल कैसे करें"),
                            ("कोडर कैंसिल", "ऑर्डर कैंसिल कैसे करें")):
            with self.subTest(heard=heard):
                bot.bestzone_product = None
                answer, score, alias = bot.match_question(heard)
                self.assertEqual(first.get(answer), want)
                self.assertGreaterEqual(score, bot_module.live_gate(alias))

    def test_call_greeting_from_dashboard_file(self):
        import json, tempfile
        from pathlib import Path
        old = bot_module.GREETING_FILE
        try:
            with tempfile.TemporaryDirectory() as folder:
                bot_module.GREETING_FILE = Path(folder) / "greeting.json"
                self.assertEqual(bot_module.load_greeting(), bot_module.HINDI_WELCOME)  # never set
                bot_module.GREETING_FILE.write_text(json.dumps({"text": " नमस्ते जी "}), encoding="utf-8")
                self.assertEqual(bot_module.load_greeting(), "नमस्ते जी")
                bot_module.GREETING_FILE.write_text(json.dumps({"text": ""}), encoding="utf-8")
                self.assertEqual(bot_module.load_greeting(), "")  # cleared: say nothing
        finally:
            bot_module.GREETING_FILE = old

    def test_pickup_delay_is_read_at_each_ring(self):
        import json, tempfile
        from pathlib import Path
        from frontbit_auto_answer import FrontbitAutoAnswer
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "pickup_delay.json"
            helper = FrontbitAutoAnswer(5.0, delay_file=path)
            helper._refresh_delay()
            self.assertEqual(helper.delay_seconds, 5.0)  # no setting yet: start-up value
            path.write_text(json.dumps({"seconds": 12}), encoding="utf-8")
            helper._refresh_delay()
            self.assertEqual(helper.delay_seconds, 12.0)
            path.write_text(json.dumps({"seconds": 999}), encoding="utf-8")
            helper._refresh_delay()
            self.assertEqual(helper.delay_seconds, 120.0)  # kept in range
            path.write_text("broken", encoding="utf-8")
            helper._refresh_delay()
            self.assertEqual(helper.delay_seconds, 120.0)  # unreadable: keep last value

    def test_greeting_waits_for_pickup(self):
        import threading, time, types
        bot = self.bare_bot()
        bot.call_active = True
        played = []
        bot._play_welcome = lambda source: played.append(time.monotonic())
        # Real call: the ring toast shows ~1 s after the call audio opens.
        auto = types.SimpleNamespace(ringing=False, answered_at=None)
        bot.auto_answer = auto
        old = bot_module.WELCOME_AFTER_PICKUP_SECONDS
        bot_module.WELCOME_AFTER_PICKUP_SECONDS = 0.2
        try:
            seen = time.monotonic()
            worker = threading.Thread(target=bot._welcome_after_pickup, args=("hands_free_session", seen))
            worker.start()
            time.sleep(1.1)
            auto.ringing = True
            time.sleep(0.8)
            self.assertEqual(played, [])  # toast late, then ringing: no greeting yet
            auto.answered_at = time.monotonic()
            auto.ringing = False
            worker.join(3)
            self.assertEqual(len(played), 1)
            self.assertGreaterEqual(played[0] - auto.answered_at, 0.15)  # after pickup + settle (Windows timer ~15 ms)
        finally:
            bot_module.WELCOME_AFTER_PICKUP_SECONDS = old

    def test_business_hours_pick_their_own_pickup_delay(self):
        import json, tempfile
        from datetime import datetime
        from pathlib import Path
        from frontbit_auto_answer import FrontbitAutoAnswer, in_business_hours
        days = {"mon": {"open": True, "hours": [["10:00", "13:00"], ["14:00", "19:00"]]},
                "tue": {"open": True, "hours": []},                    # open, no hours: all day
                "fri": {"open": True, "hours": [["22:00", "02:00"]]},  # past midnight
                "sun": {"open": False, "hours": [["10:00", "19:00"]]}}  # closed: hours ignored
        monday = datetime(2026, 11, 2)  # a Monday
        at = lambda day_offset, hh, mm: monday.replace(day=2 + day_offset, hour=hh, minute=mm)
        self.assertTrue(in_business_hours(at(0, 10, 0), days))
        self.assertFalse(in_business_hours(at(0, 13, 30), days))  # lunch gap
        self.assertFalse(in_business_hours(at(0, 19, 0), days))   # closes at 19:00
        self.assertTrue(in_business_hours(at(1, 3, 0), days))     # Tuesday all day
        self.assertFalse(in_business_hours(at(2, 12, 0), days))   # Wednesday not set
        self.assertTrue(in_business_hours(at(4, 23, 0), days))    # Friday night
        self.assertTrue(in_business_hours(at(5, 1, 30), days))    # ... into Saturday
        self.assertFalse(in_business_hours(at(5, 2, 30), days))
        self.assertFalse(in_business_hours(at(6, 12, 0), days))   # Sunday closed
        with tempfile.TemporaryDirectory() as folder:
            normal, business = Path(folder) / "pickup_delay.json", Path(folder) / "business_hours.json"
            normal.write_text(json.dumps({"seconds": 5}), encoding="utf-8")
            business.write_text(json.dumps({"seconds": 2, "days": days}), encoding="utf-8")
            helper = FrontbitAutoAnswer(5.0, delay_file=normal, business_file=business)
            helper._refresh_delay(now=at(0, 11, 0))
            self.assertEqual((helper.delay_seconds, helper.delay_reason), (2.0, "business_hours"))
            helper._refresh_delay(now=at(0, 20, 0))
            self.assertEqual((helper.delay_seconds, helper.delay_reason), (5.0, "default"))
            business.write_text("broken", encoding="utf-8")
            helper._refresh_delay(now=at(0, 11, 0))
            self.assertEqual(helper.delay_seconds, 5.0)  # unreadable hours: normal delay

    def test_topic_hint_leak(self):
        # Noise heard as the hint words themselves is not trusted alone.
        for text in ("वेंडर", "जीएसटी बिल रिफंड", "कैंसिल", ""):
            self.assertTrue(bot_module.is_topic_hint_leak(text), text)
        for text in ("सीओडी है क्या", "ऑर्डर कैंस सील करना है", "डिस्काउंड कितना मिलेगा"):
            self.assertFalse(bot_module.is_topic_hint_leak(text), text)

    def test_only_grammar_words_get_no_answer(self):
        # STT dropped the topic word ("ऑर्डर कैसे दूँ" -> "कैसे"): ask again, never guess.
        bot = self.bare_bot()
        for heard in ("कैसे", "कैसे करें", "कैसे करे"):
            with self.subTest(heard=heard):
                answer, score, alias = bot.match_question(heard)
                self.assertTrue(answer is None or score < bot_module.live_gate(alias))

    def test_garbled_background_gets_no_answer(self):
        bot = self.bare_bot()
        for heard in ("दिखोन मीदा है क्या? यी प्र्ड क्या से मिलेगा?", "यर यर सब्च्ट्रीं यर यर सब्च",
                      "अदेज योर माइद किने अदेज योर माइद", "मूल सेल क्या", "कल बात करते हैं", "ठीक है"):
            with self.subTest(heard=heard):
                answer, score, alias = bot.match_question(heard)
                self.assertTrue(answer is None or score < bot_module.live_gate(alias))

    def test_same_answer_is_not_repeated_back_to_back(self):
        bot = self.bare_bot()
        audio = np.full(32000, 0.05, dtype=np.float32)
        bot.transcribe_hindi = Mock(return_value=("वॉल हुक की कीमत क्या है", 0.3))
        bot.process_audio(audio, datetime.now(), 23, True)
        bot.transcribe_hindi = Mock(return_value=("वॉल हुक का रेट बताइए", 0.3))
        bot.process_audio(audio, datetime.now(), 24, True)
        speaks = [c for c in bot.voice.send.call_args_list if c.args and c.args[0] == "speak"]
        self.assertEqual(len(speaks), 1)

    def test_bot_echo_is_not_answered(self):
        bot = self.bare_bot()
        bot.remember_bot_speech(HINDI_REPEAT_PROMPT)
        bot.voice_active.set()
        bot.transcribe_hindi = Mock(return_value=("मैं आपका सवाल समझ नहीं बाया", 0.3))
        bot.process_audio(np.full(32000, 0.05, dtype=np.float32), datetime.now(), 11, True)
        bot.voice.send.assert_not_called()
        self.assertEqual(bot.set_status.call_args.kwargs["skipped"], "bot_echo_rejected")


if __name__ == "__main__":
    unittest.main()
