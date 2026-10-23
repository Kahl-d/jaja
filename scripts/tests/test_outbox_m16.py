"""outbox.py lint M16: a comment or post never asks for a job; it says what the author thinks about the post's subject.
Refused in linkedin-comment, linkedin-post, x-reply and x-post (phrases: policy.outreach.job_ask_patterns): "hire me",
"open to work", "DM me", "I'm looking", "please refer", "my resume"."""
from __future__ import annotations

import pytest

import comms_helpers as ch  # noqa: F401  (puts scripts/ on sys.path)
from comms_helpers import offline  # noqa: F401  (autouse: no network)
import outbox as ob

VIEW = ("Evals that come from real use read truer than synthetic ones: we graded each agent run against the saved design "
        "file, and the prompts improved every round.")


@pytest.mark.parametrize("ask", ["Great post! Hire me, I build agents.", "I'm open to work if your team needs evals help.",
                                 "DM me if you are hiring.", "I'm looking for a role like this.", "I am looking for my next team.",
                                 "Please refer me for this one.", "Happy to send my resume.", "Would love a referral, my résumé is linked."])
@pytest.mark.parametrize("channel", ["linkedin-comment", "linkedin-post", "x-reply", "x-post"])
def test_a_job_ask_in_a_comment_or_post_is_refused(channel, ask):
    issues = ob.lint_draft(channel, ask)
    assert any(i.startswith("M16:") for i in issues), issues


@pytest.mark.parametrize("channel", ["linkedin-comment", "linkedin-post", "x-reply", "x-post"])
def test_a_view_on_the_subject_passes_m16(channel):
    assert not [i for i in ob.lint_draft(channel, VIEW) if i.startswith("M16:")]


def test_m16_is_for_public_comments_and_posts_only():
    """A message to a person may mention a resume (a recruiter asked for it); M16 is about public presence."""
    assert not [i for i in ob.lint_draft("email", "Thanks, here is my resume as you asked.") if i.startswith("M16:")]
    assert not [i for i in ob.lint_draft("linkedin-message", "Here is my resume.") if i.startswith("M16:")]
