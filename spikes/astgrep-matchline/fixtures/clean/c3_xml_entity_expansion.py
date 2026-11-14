"""Clean counterpart for R3 (no-xml-entity-expansion).

The XML parser is built with entity expansion switched off.
Expected: zero findings.
"""

from lxml import etree


def build_parser() -> etree.XMLParser:
    """Return a parser that does not expand entities."""
    return etree.XMLParser(resolve_entities=False, huge_tree=False)
