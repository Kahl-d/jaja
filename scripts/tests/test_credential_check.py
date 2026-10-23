"""credential_check.py: one detector for credentials in free text (apply_pack.py account notes, the accounts
registry, lint and every record value a pack copies). Every secret below is fake."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1]
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))
import credential_check as cc  # noqa: E402

SECRET = "Tidepool!2026"


@pytest.mark.parametrize("text", [
    # keyword with a colon, "=", a dash or none, in any case
    f"password: {SECRET}", f"Password:{SECRET}", f"PASSWORD={SECRET}", f"password = {SECRET}",
    f"password - {SECRET}", f"password {SECRET}", "password tidepool", f"passwd: {SECRET}", f"pwd {SECRET}",
    f"pw {SECRET}", "PW=tidepool", f"p/w: {SECRET}", f"pass: {SECRET}",
    # "is" and "was" forms
    f"my password is {SECRET}", "My Password Is tidepool", f"the pw is {SECRET}", "my password was tidepool",
    f"password is: {SECRET}", f"{SECRET} is my password",
    # login and user words
    f"login {SECRET}", "login: alexex", f"my login is {SECRET}", "username: alexex", "user id aex2026",
    # passcodes, PINs, secrets and codes
    "passcode 884422", "Passcode: tidepool", "PASSCODE=4411", "pin 1234", "PIN: 1234", "pin=1234",
    "my pin is 1234", "pin code 1234", "secret: abc123", f"secret {SECRET}", "the secret is tidepool",
    "client secret is abc123", "security answer: fluffy", "secret question answer is fluffy",
    "recovery code 8F3K-22LQ", "backup codes 1234-5678", "2fa code 123456", "otp 123456",
    "one-time code: 123456", "verification code 482913", "api key: abc123def", "access token Xy12.ab34.cd56",
    # known key prefixes
    "key sk" + "-proj-Ab3dE5fG7hJ9kL1mN3pQ", "sk" + "-ant-api03-Ab3dE5fG7hJ9kL1mN3pQrS",
    "gh" + "p_16C7e42F292c6912E7710c838347Ae178B4a",   # split so secret scanners see no token
    "github_pat_11ABCDEFG0123456789_abcdefghij",
    "xox" + "b-123456789012-abcdefghijkl", "AKIA" + "IOSFODNN7EXAMPLE", "ya29" + ".a0AfH6SMBx-EXAMPLEtoken",
    "GOCSPX-abcdefghijklmnop", "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.abcDEF123",
    "-----BEGIN RSA PRIVATE" + " KEY-----",
    # token-shaped strings, 20 or more mixed characters
    "aB3dE5fG7hJ9kL1mN3pQ", "note wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY here",
    "Tidepool2026Tidepool2026", "b94d2bb934d7fc65ee55ddd3ec5941af",
])
def test_credentials_are_found(text):
    reason = cc.find_credential(text)
    assert reason, text
    assert SECRET not in reason and "tidepool" not in reason.lower() and "1234" not in reason


@pytest.mark.parametrize("text", [
    "created via SSO", "email alex@example.test used", "",
    "sign in with Google; no separate password", "login via LinkedIn", "uses the LinkedIn login",
    "password reset link goes to the job-search Gmail", "the password is set by Alex himself",
    "PIN not required", "the portal emails a one-time code at each sign-in", "verification code by SMS",
    "Workday session times out after 15 minutes: save each page", "password-protected PDF upload refused",
    "account created 2026-09-27 by Alex", "tenant quarrylabs.wd1.myworkdayjobs.com/en-US/QuarryCareers",
    "https://quarrylabs.wd1.myworkdayjobs.com/en-US/QuarryCareers/job/Machine-Learning-Engineer_QL-2231",
    "my login is alex@example.test", "portal asks for a cover letter upload",
    "lumenloop-ai-agent-platform-engineer-202609", "-".join(("0f1e2d3c", "4b5a", "6978", "8a9b", "0c1d2e3f4a5b")),
    "task-oriented agents; scikit-learn and sk-learn style pipelines",
    "Yes, I will need sponsorship in the future, not to start. Details on request.",
    "Machine Learning Engineer, Northwind Robotics (since 2025-01)", "+1 (555) " + "010-0199",
    "Alex creates the account and types the password himself; apply-assist never sees it",
    "New user registration (username, password)", "no password, SSO only", "token expires after an hour",
    # prose from the department's own files: a credential word followed by an ordinary word, a path or an aside
    "fatal on submodule pin drift", "post-login warm-ups", "the Gmail token exists", "a sign-in wall appears",
    "Workday tokens are `<host>/<tenant>/<site>`", "needs Streaming Premium login (ev-001)",
    "signatures and passwords are Alex's", "watch log in `research/job-platforms-2026.md`",
    "https://boards-api.greenhouse.io/v1/boards/{token}/jobs", "pin-bump-only release",
    "profile/_inbox/latest-resume/AlexExample_Resume_2026-05-26.txt", "feat/ab-74-HubTool-skill-library",
    "Jobright Daily-H1B-Jobs-In-Tech (GitHub)",
])
def test_benign_text_passes(text):
    assert cc.find_credential(text) is None, text


def test_non_text_is_never_a_credential():
    assert cc.find_credential(None) is None and cc.find_credential(1234) is None
    assert cc.find_credential(True) is None
