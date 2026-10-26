"""Resolve supported saved locations against the actual EPUB content."""
import posixpath
import re
import zipfile
from pathlib import Path
from urllib.parse import unquote
from xml.etree import ElementTree as ET
from xml.parsers import expat


def xml(data: bytes):
    if len(data) > 16_000_000:
        raise ValueError("Unsupported or oversized EPUB XML")
    # Permit standard XHTML declarations, without reading DTDs or expanding
    # declarations. Expat recognizes declarations even in UTF-16 XML.
    parser = expat.ParserCreate()
    def doctype(name, system_id, public_id, internal_subset):
        if internal_subset:
            raise ValueError("Unsupported EPUB XML internal subset")
    def entity(*args):
        raise ValueError("Unsupported EPUB XML entity declaration")
    parser.StartDoctypeDeclHandler = doctype
    parser.EntityDeclHandler = entity
    parser.ExternalEntityRefHandler = entity
    parser.SetParamEntityParsing(expat.XML_PARAM_ENTITY_PARSING_NEVER)
    try:
        parser.Parse(data, True)
        return ET.fromstring(data)
    except (expat.ExpatError, ET.ParseError) as error:
        raise ValueError("Invalid EPUB XML: " + str(error)) from error


def local(tag):
    return tag.rsplit("}", 1)[-1]


def passage(epub: str | Path, locator: str) -> str:
    location = unquote(locator.rsplit("!", 1)[-1])
    chapter, sep, fragment = location.partition("#")
    chapter = chapter.removeprefix("/")
    if not sep or not fragment:
        raise ValueError("Unsupported Kobo locator; a chapter and precise fragment are required")
    with zipfile.ZipFile(epub) as z:
        container = xml(z.read("META-INF/container.xml"))
        rootfiles = [e for e in container.iter() if local(e.tag) == "rootfile"]
        if not rootfiles:
            raise ValueError("EPUB has no package document")
        opf_path = rootfiles[0].attrib["full-path"]
        opf = xml(z.read(opf_path))
        manifest = {e.attrib["id"]: e for e in opf.iter() if local(e.tag) == "item"}
        names = []
        for e in opf.iter():
            if local(e.tag) == "itemref" and e.attrib.get("idref") in manifest:
                item = manifest[e.attrib["idref"]]
                names.append(posixpath.normpath(posixpath.join(posixpath.dirname(opf_path), unquote(item.attrib["href"]))))
        candidates = [n for n in names if n == chapter or n == posixpath.normpath(posixpath.join(posixpath.dirname(opf_path), chapter))]
        if not candidates:
            candidates = [n for n in names if posixpath.basename(n) == chapter]
        if len(candidates) != 1:
            raise ValueError("Kobo chapter does not uniquely match the supplied EPUB edition")
        info = z.getinfo(candidates[0])
        if info.file_size > 16_000_000:
            raise ValueError("Oversized EPUB chapter")
        root = xml(z.read(candidates[0]))
        targets = [e for e in root.iter() if e.attrib.get("id") == fragment]
        if len(targets) != 1:
            raise ValueError("Kobo fragment not found uniquely in EPUB; supply the exact Kobo edition (CFI locators are not yet supported)")
        target = targets[0]
        chunks = []
        started = False

        def visit(node):
            nonlocal started
            if local(node.tag) in {"script", "style", "head"}:
                return
            if node is target:
                started = True
            if started and node.text:
                chunks.append(node.text)
            for child in node:
                visit(child)
                if started and child.tail:
                    chunks.append(child.tail)
            if started and local(node.tag) in {"p", "div", "section", "br", "li"}:
                chunks.append(" ")

        visit(root)
        text = re.sub(r"\s+", " ", "".join(chunks)).strip()
        if not text:
            raise ValueError("Kobo fragment has no following text")
        return text[:4000]
