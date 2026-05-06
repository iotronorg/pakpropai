"""
Base classes for the modular scraper system.

To add a new site:
1. Create apps/properties/scrapers/<site>.py
2. Subclass BaseScraper, set site_name, implement search()
3. Import and add to SCRAPERS list in registry.py
"""
from __future__ import annotations
from dataclasses import dataclass, asdict, field
from typing import Optional


@dataclass
class PropertyResult:
    source:               str
    source_id:            str
    title:                str
    city:                 str
    location:             str
    area_marla:           Optional[float] = None
    price_pkr:            Optional[int]   = None
    property_type:        str             = 'residential'
    furnished_status:     Optional[str]   = None   # furnished / unfurnished / semi_furnished
    construction_status:  Optional[str]   = None   # builder / ready / under_construction
    url:                  str             = ''
    ai_score:             Optional[int]   = None
    ai_verdict:           str             = ''

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> 'PropertyResult':
        valid_keys = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in d.items() if k in valid_keys})

    def format_wa(self, index: int = None) -> str:
        prefix = f"{index}. " if index is not None else "• "
        price  = f"PKR {self.price_pkr:,}" if self.price_pkr else "Price TBD"
        area   = f"{self.area_marla}M" if self.area_marla else ""
        score  = f" | Score {self.ai_score}/100" if self.ai_score else ""
        badge  = " [verified]" if self.source == 'pakprop' else f" [{self.source}]"

        tags = []
        if self.furnished_status:
            tags.append(self.furnished_status.replace('_', '-'))
        if self.construction_status:
            tags.append(self.construction_status.replace('_', ' '))
        tag_str = f" | {', '.join(tags)}" if tags else ""

        parts = " | ".join(p for p in [area, price] if p)

        lines = [f"{prefix}{self.title}"]
        lines.append(f"   {self.location}, {self.city} | {parts}{tag_str}{score}{badge}")
        if self.ai_verdict:
            lines.append(f"   _{self.ai_verdict}_")
        if self.url:
            lines.append(f"   {self.url}")
        return "\n".join(lines)


class BaseScraper:
    site_name: str  = ''
    ENABLED:   bool = True

    def search(self, city: str = '', location: str = '',
               area_marla: float = None, max_price: int = None,
               property_type: str = '') -> list[PropertyResult]:
        raise NotImplementedError

    # ── shared helpers ────────────────────────────────────────────────────────

    @staticmethod
    def parse_pkr(text: str) -> Optional[int]:
        import re
        t = text.lower().replace(',', '').replace('rs.', '').replace('pkr', '').strip()
        for pattern, mul in [
            (r'(\d+(?:\.\d+)?)\s*crore',       10_000_000),
            (r'(\d+(?:\.\d+)?)\s*(?:lakh|lac)', 100_000),
            (r'(\d+(?:\.\d+)?)\s*million',      1_000_000),
        ]:
            m = re.search(pattern, t)
            if m:
                return int(float(m.group(1)) * mul)
        m = re.search(r'\b(\d{7,})\b', t)
        return int(m.group(1)) if m else None

    @staticmethod
    def parse_area(text: str) -> Optional[float]:
        import re
        t = text.lower()
        m = re.search(r'(\d+(?:\.\d+)?)\s*(?:marla|mrla)', t)
        if m: return float(m.group(1))
        m = re.search(r'(\d+(?:\.\d+)?)\s*kanal', t)
        if m: return float(m.group(1)) * 20
        m = re.search(r'(\d+(?:\.\d+)?)\s*(?:sq\.?\s*ft|sqft)', t)
        if m: return round(float(m.group(1)) / 272.25, 2)
        # 1 marla = 30.25 sq yd
        m = re.search(r'(\d+(?:\.\d+)?)\s*(?:sq\.?\s*yd|sqyd|square\s*yard)', t)
        if m: return round(float(m.group(1)) / 30.25, 2)
        return None
