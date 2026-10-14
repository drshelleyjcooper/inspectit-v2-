"""Terms of Service and Privacy Policy pages: served, linked, and consistent with the product."""


def test_pages_are_served_and_indexable(client):
    for path, h1 in (("/terms", "Terms of Service"), ("/privacy", "Privacy Policy")):
        r = client.get(path)
        assert r.status_code == 200 and "text/html" in r.headers["content-type"]
        assert f"<h1>{h1}</h1>" in r.text
        assert 'lang="en"' in r.text and "noindex" not in r.text      # public pages: indexable
        assert 'href="#main"' in r.text                               # skip link


def test_pages_cross_link_and_footer_links_exist(client):
    t, p = client.get("/terms").text, client.get("/privacy").text
    assert 'href="/privacy"' in t and 'href="/terms"' in p
    for page in (client.get("/").text, client.get("/forgot-password").text,
                 client.get("/reset-password").text, t, p):
        assert 'href="/terms"' in page and 'href="/privacy"' in page


def test_signup_form_links_the_legal_pages(client):
    home = client.get("/").text
    assert 'href="/terms"' in home.split("By signing up")[1][:400]
    assert 'href="/privacy"' in home.split("By signing up")[1][:700]


def test_terms_match_the_trial_timeline_in_code(client):
    """If the timeline settings change, the published terms must change too."""
    from api import config
    t = client.get("/terms").text + client.get("/privacy").text
    assert config.TRIAL_DAYS == 30 and "30-day" in t
    assert config.TRIAL_GRACE_DAYS == 7 and "7 more days" in t
    assert config.DATA_RETENTION_DAYS == 30 and "30 more days" in t
    assert config.RETENTION_WARNING_DAYS == 7 and "about 7 days before" in t
    assert config.TRIAL_REMINDER_DAYS == 7


def test_every_privacy_claim_about_providers_is_named(client):
    p = client.get("/privacy").text
    for name in ("DigitalOcean", "Resend", "Microsoft 365", "YouTube"):
        assert name in p


def test_policies_describe_the_two_step_deletion(client):
    from api import config
    t, p = client.get("/terms").text, client.get("/privacy").text
    assert config.HARD_PURGE_DELAY_DAYS == 7
    assert "kept out of reach for 7 more days" in t and "permanently deleted" in t
    assert "hidden for 7 more days" in p and "files you uploaded" in p
    assert "accounts of members who belonged only to that organization" in p


def test_filled_in_details_are_present_and_filled_placeholders_are_gone(client):
    t, p = client.get("/terms").text, client.get("/privacy").text
    for page in (t, p):
        assert "1290 W. Gregory Blvd., Kansas City, MO 64114" in page
        for gone in ("LEGAL BUSINESS NAME", "[STATE]", "COUNTY / STATE", "OPTIONAL: MAILING ADDRESS",
                     "PAYMENT PROVIDER"):
            assert gone not in page
    assert "State of Missouri" in t and "Jackson County, Missouri" in t
    assert "Subscriptions are for one year" in t
    assert "Stripe" in p and "Stripe" in t


def test_pricing_section_says_subscriptions_are_one_year(client):
    home = client.get("/").text
    assert "Subscriptions are for one year" in home and "Plans and prices coming" not in home
