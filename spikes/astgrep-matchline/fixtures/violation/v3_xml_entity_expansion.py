"""Seeded violation for R3 (no-xml-entity-expansion).

Expected: `no-xml-entity-expansion` fires exactly once in this file.
"""

from lxml import etree

parser = etree.XMLParser(resolve_entities=True, huge_tree=True)
