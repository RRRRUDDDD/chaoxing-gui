# Verification-page fixtures

These files are **synthetic**. No response from a throttled real account has
been captured yet. They encode the only facts the code relies on: the
verification page loads `/processVerifyPng.ac` and submits to
`/html/processVerify.ac`, either via a redirect `Location` or inside the page.

Replace or extend them with captured samples (status, `Location`, headers and
the first 2 KB of the body, with cookies and personal data removed) before
widening `api.captcha.is_captcha_response`.

`CAPTCHA_PROTOCOL_VERIFIED` currently remains false. The production flow reports that manual verification is required without fetching or submitting a captcha. Offline tests explicitly patch this guard to exercise the framework; they do not establish live-platform behavior.
