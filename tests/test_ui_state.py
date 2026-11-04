import unittest

from focus_blocker.ui_state import CompletionTracker, StopAuthorization


class UIStateTests(unittest.TestCase):
    def test_back_before_authorization_returns_rejects_late_token(self):
        flow = StopAuthorization()
        attempt = flow.generation
        flow.invalidate()
        self.assertFalse(flow.accept(attempt, "late-token"))
        self.assertIsNone(flow.token)

    def test_close_after_worker_before_ui_callback_revokes_token(self):
        flow = StopAuthorization()
        attempt = flow.generation
        self.assertTrue(flow.accept(attempt, "token"))
        self.assertEqual(flow.invalidate(), "token")
        self.assertFalse(flow.matches(attempt))
        self.assertIsNone(flow.token)

    def test_superseded_attempt_cannot_replace_current_token(self):
        flow = StopAuthorization()
        old = flow.generation
        flow.invalidate()
        self.assertTrue(flow.accept(flow.generation, "current"))
        self.assertFalse(flow.accept(old, "old"))
        self.assertEqual(flow.token, "current")

    def test_notification_waits_for_release_and_fires_once(self):
        tracker = CompletionTracker()
        self.assertFalse(tracker.update({"active": None}))
        self.assertFalse(tracker.update({"active": {"id": "a"}}))
        for _ in range(3):
            self.assertFalse(tracker.update({"active": None, "release_pending": True}))
        self.assertTrue(tracker.update({"active": None, "release_pending": False}))
        self.assertFalse(tracker.update({"active": None}))


class CompanionTests(unittest.TestCase):
    def test_session_animation_preference_is_backward_compatible_and_optional(self):
        from focus_blocker.rules import Invalid, configuration
        from focus_blocker.ui_state import CompanionState

        self.assertTrue(configuration({})["show_animation"])
        self.assertFalse(configuration({"show_animation": False})["show_animation"])
        for invalid in (0, 1, "false", None):
            with self.assertRaises(Invalid):
                configuration({"show_animation": invalid})
        view = CompanionState()
        view.status = {"active": {"id": "test"}}
        self.assertTrue(view.visible(0))
        view.status["active"]["config"] = {"show_animation": False}
        self.assertFalse(view.visible(0))

    def test_progress_is_a_clamped_service_sample_with_display_only_interpolation(self):
        from focus_blocker.ui_state import SessionProgress

        progress = SessionProgress()
        progress.update({"duration": 60, "remaining": 60}, 100)
        self.assertEqual(progress.fraction(100), 1)
        self.assertEqual(progress.fraction(130), 0.5)
        self.assertEqual(progress.tooltip(130), "0:30 remaining")
        self.assertEqual(progress.fraction(165), 0)
        self.assertEqual(progress.tooltip(165), "0:00 remaining")
        # The next authoritative service sample replaces display interpolation.
        progress.update({"duration": 60, "remaining": 12}, 170)
        self.assertEqual(progress.fraction(170), 0.2)
        progress.update({"duration": 60, "remaining": 100}, 200)
        self.assertEqual(progress.fraction(200), 1)
        progress.update({"duration": 7200, "remaining": 3661}, 220)
        self.assertEqual(progress.tooltip(220), "1:01:01 remaining")
        progress.update({"config": {"minutes": 2}, "remaining": 90}, 230)
        self.assertEqual(progress.fraction(230), 0.75)
        progress.update(None, 240)
        self.assertEqual(progress.fraction(240), 0)

    def test_visibility_tracks_service_and_window_without_timer_authority(self):
        from focus_blocker.ui_state import CompanionState

        view = CompanionState()
        self.assertFalse(view.visible(100))
        view.status = {"starting": True, "active": None}
        self.assertFalse(view.visible(100))
        view.status = {"active": {"id": "test"}}
        self.assertTrue(view.visible(100))
        view.ui_open = True
        self.assertFalse(view.visible(100))
        view.ui_open = False
        view.opening_until = 105
        self.assertFalse(view.visible(104))
        self.assertTrue(
            view.visible(105), "Failed UI launch must not permanently hide the animation"
        )
        view.status = None
        self.assertFalse(view.visible(106), "Connection loss must hide stale animation")
        view.status = {"active": None, "release_pending": True}
        self.assertFalse(view.visible(107))
