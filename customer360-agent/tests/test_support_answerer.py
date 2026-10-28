"""Use case 1: support answers grounded in the knowledge base, with a web
fallback that is restricted to the support domain in code, not just in the
prompt."""

from use_cases.support_answerer import (
    MAX_PASSAGE_CHARS,
    MAX_PASSAGES,
    NOT_FOUND,
    answer_support_question,
)

DOMAIN = "help.example.test"


def _passage(url, text="Reset your password from Settings.", title="Passwords"):
    return {"url": url, "title": title, "text": text}


class _Scripted:
    def __init__(self, *replies):
        self.replies = list(replies)
        self.prompts: list[str] = []

    def complete(self, prompt: str) -> str:
        self.prompts.append(prompt)
        return self.replies.pop(0)


class _Spy:
    """A search callable that records calls and returns fixed passages."""

    def __init__(self, passages):
        self.passages = passages
        self.calls: list[tuple] = []

    def __call__(self, *args):
        self.calls.append(args)
        return self.passages


def test_kb_answer_is_returned_with_its_sources():
    kb = _Spy([_passage(f"https://{DOMAIN}/passwords")])
    web = _Spy([])

    result = answer_support_question("How do I reset my password?", _Scripted("Use Settings."), kb, web, DOMAIN)

    assert result.found is True
    assert result.answer == "Use Settings."
    assert result.source_kind == "kb"
    assert result.sources == [f"https://{DOMAIN}/passwords"]
    assert web.calls == []


def test_not_found_without_a_fallback_gives_no_invented_answer():
    kb = _Spy([_passage(f"https://{DOMAIN}/passwords")])

    result = answer_support_question("What is your refund policy?", _Scripted(NOT_FOUND), kb)

    assert result.found is False
    assert result.source_kind is None
    assert result.sources == []


def test_kb_miss_falls_back_to_a_search_restricted_to_the_domain():
    kb = _Spy([_passage(f"https://{DOMAIN}/passwords")])
    web = _Spy([_passage(f"https://{DOMAIN}/refunds", text="Refunds take 5 business days.", title="Refunds")])
    provider = _Scripted(NOT_FOUND, "Refunds take 5 business days.")

    result = answer_support_question("How long do refunds take?", provider, kb, web, DOMAIN)

    assert web.calls == [("How long do refunds take?", DOMAIN)]
    assert result.found is True
    assert result.source_kind == "web"
    assert result.sources == [f"https://{DOMAIN}/refunds"]


def test_results_from_other_domains_are_dropped():
    web = _Spy([
        _passage("https://evil.example.test/a"),
        _passage(f"https://{DOMAIN}.evil.test/b"),
        _passage(f"https://evil{DOMAIN}/c"),
        _passage(f"https://docs.{DOMAIN}/ok", text="Allowed subdomain."),
    ])
    provider = _Scripted("From the allowed page.")

    result = answer_support_question("q", provider, _Spy([]), web, DOMAIN)

    assert result.sources == [f"https://docs.{DOMAIN}/ok"]
    assert "evil" not in provider.prompts[0]


def test_if_every_web_result_is_off_domain_nothing_is_answered():
    web = _Spy([_passage("https://evil.example.test/a")])
    provider = _Scripted()

    result = answer_support_question("q", provider, _Spy([]), web, DOMAIN)

    assert result.found is False
    assert provider.prompts == []


def test_empty_kb_goes_straight_to_the_fallback_without_a_model_call():
    web = _Spy([_passage(f"https://{DOMAIN}/x")])
    provider = _Scripted("Answer from the web.")

    answer_support_question("q", provider, _Spy([]), web, DOMAIN)

    assert len(provider.prompts) == 1


def test_no_kb_hits_and_no_fallback_is_not_found_without_a_model_call():
    provider = _Scripted()

    result = answer_support_question("q", provider, _Spy([]))

    assert result.found is False
    assert provider.prompts == []


def test_both_stages_not_found_is_not_found():
    kb = _Spy([_passage(f"https://{DOMAIN}/a")])
    web = _Spy([_passage(f"https://{DOMAIN}/b")])

    result = answer_support_question("q", _Scripted(NOT_FOUND, f"  {NOT_FOUND} \n"), kb, web, DOMAIN)

    assert result.found is False


def test_passages_are_capped_in_count_and_length():
    long_text = "z" * (MAX_PASSAGE_CHARS + 300)
    kb = _Spy([_passage(f"https://{DOMAIN}/p{i}", text=long_text) for i in range(MAX_PASSAGES + 3)])
    provider = _Scripted("ok")

    result = answer_support_question("q", provider, kb)

    assert "z" * MAX_PASSAGE_CHARS in provider.prompts[0]
    assert "z" * (MAX_PASSAGE_CHARS + 1) not in provider.prompts[0]
    assert len(result.sources) == MAX_PASSAGES


def test_em_dashes_are_replaced_in_the_answer():
    kb = _Spy([_passage(f"https://{DOMAIN}/a")])

    result = answer_support_question("q", _Scripted("Open Settings — then Security."), kb)

    assert result.answer == "Open Settings, then Security."
