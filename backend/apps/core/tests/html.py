"""HTML checks shared by the tests of the pages."""

from html.parser import HTMLParser


class InlineCode(HTMLParser):
    """What the policy would block: inline scripts and style attributes."""

    def __init__(self):
        super().__init__()
        self.scripts = []
        self.styles = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if "style" in attrs:
            self.styles.append(tag)
        # A script runs inline unless it has src or a data type such as JSON.
        runs = attrs.get("type", "text/javascript") in ("text/javascript", "module")
        if tag == "script" and "src" not in attrs and runs:
            self.scripts.append(attrs.get("id", "?"))


def inline_code(html):
    parser = InlineCode()
    parser.feed(html)
    return parser.scripts, parser.styles
