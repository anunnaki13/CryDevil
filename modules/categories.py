"""
Result category parser — konversi hasil 4D menjadi kategori BK/GJ per posisi.

Aturan:
  Besar/Kecil  → ditentukan oleh digit PERTAMA pasangan
    0,1,2,3,4 = Kecil  |  5,6,7,8,9 = Besar
  Genap/Ganjil → ditentukan oleh digit KEDUA pasangan
    0,2,4,6,8 = Genap  |  1,3,5,7,9 = Ganjil

Contoh hasil 4D "1295":
  depan    = "12" → Kecil (1<5) + Genap (2%2==0)
  tengah   = "29" → Kecil (2<5) + Ganjil (9%2!=0)
  belakang = "95" → Besar (9>=5) + Ganjil (5%2!=0)
"""

# ─── Kategori dasar ───────────────────────────────────────────────────────────

BESAR_KECIL_SMALL = {0, 1, 2, 3, 4}   # digit pertama → Kecil
GENAP_DIGITS      = {0, 2, 4, 6, 8}   # digit kedua   → Genap


def classify_pair(pair: str) -> dict:
    """Klasifikasi satu pasangan 2 digit → BK dan GJ."""
    d1, d2 = int(pair[0]), int(pair[1])
    return {
        "pair":          pair,
        "besar_kecil":   "kecil" if d1 in BESAR_KECIL_SMALL else "besar",
        "genap_ganjil":  "genap" if d2 in GENAP_DIGITS else "ganjil",
    }


def parse_result(result_4d: str) -> dict | None:
    """
    Parse hasil 4D menjadi dict kategori per posisi.

    Args:
        result_4d: string 4 digit, misal "1295"

    Returns:
        {
            "raw": "1295",
            "depan":    {"pair": "12", "besar_kecil": "kecil",  "genap_ganjil": "genap"},
            "tengah":   {"pair": "29", "besar_kecil": "kecil",  "genap_ganjil": "ganjil"},
            "belakang": {"pair": "95", "besar_kecil": "besar",  "genap_ganjil": "ganjil"},
        }
    """
    import re
    digits = re.sub(r"\D", "", str(result_4d).strip())
    if len(digits) < 4:
        return None
    digits = digits[-4:]  # ambil 4 digit terakhir jika lebih

    return {
        "raw":      digits,
        "depan":    classify_pair(digits[0:2]),
        "tengah":   classify_pair(digits[1:3]),
        "belakang": classify_pair(digits[2:4]),
    }


# ─── Generator angka per kategori ────────────────────────────────────────────

def get_numbers_for_category(category: str) -> list[str]:
    """
    Kembalikan semua 50 angka 2D yang memenuhi kategori.

    category: "besar" | "kecil" | "genap" | "ganjil"
    """
    result = []
    for n in range(100):
        d1, d2 = n // 10, n % 10
        num = f"{n:02d}"
        if category == "besar"  and d1 not in BESAR_KECIL_SMALL: result.append(num)
        if category == "kecil"  and d1 in BESAR_KECIL_SMALL:     result.append(num)
        if category == "genap"  and d2 in GENAP_DIGITS:           result.append(num)
        if category == "ganjil" and d2 not in GENAP_DIGITS:       result.append(num)
    return result


# ─── Helper ringkasan ─────────────────────────────────────────────────────────

def result_summary(parsed: dict) -> str:
    """Format ringkas: '1295 | Depan:Kecil/Genap | Tengah:Kecil/Ganjil | Belakang:Besar/Ganjil'"""
    parts = []
    for pos in ("depan", "tengah", "belakang"):
        info = parsed[pos]
        parts.append(f"{pos.capitalize()}:{info['besar_kecil'].capitalize()}/{info['genap_ganjil'].capitalize()}")
    return f"{parsed['raw']} | " + " | ".join(parts)
