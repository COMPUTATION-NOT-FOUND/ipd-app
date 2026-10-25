"""Honest strategy names containing words like "script" are accepted (review, Oct 2026: the XSS
word list rejected "Postscript Bot" and "Subscriber" although the charset rule already blocks
markup). Markup is still rejected."""
import pytest

from app import validate_name


@pytest.mark.parametrize('name', ['Postscript Bot', 'Subscriber', 'onload_balancer', "Hijab's strategy"])
def test_honest_names_pass(name):
    assert validate_name(name, field_type='strategy name') == (True, "")


@pytest.mark.parametrize('name', ['<script>x</script>', 'javascript:alert(1)', 'a" onclick="x', 'a&b'])
def test_markup_still_fails(name):
    assert validate_name(name, field_type='strategy name')[0] is False


def test_other_fields_keep_the_word_list():
    assert validate_name('my script', field_type='tournament name')[0] is False
