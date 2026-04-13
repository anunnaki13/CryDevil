"""
Klasifikasi hasil 2D ke kategori Besar/Kecil dan Genap/Ganjil.

Aturan (sesuai blueprint):
  Besar/Kecil → nilai KESELURUHAN angka 2D:
    KECIL (KE) = 00–49  (digit pertama 0,1,2,3,4)
    BESAR (BE) = 50–99  (digit pertama 5,6,7,8,9)

  Genap/Ganjil → digit KEDUA (satuan) angka 2D:
    GENAP  (GE) = digit satuan 0,2,4,6,8
    GANJIL (GA) = digit satuan 1,3,5,7,9

Contoh "95":
  95 >= 50        → BE (BESAR)
  digit satuan 5  → GA (GANJIL)
"""

# ─── Label kode ───────────────────────────────────────────────────────────────

CHOICE_LABELS = {
    "BE": "BESAR",
    "KE": "KECIL",
    "GE": "GENAP",
    "GA": "GANJIL",
}

POSITION_LABELS = {
    "depan": "DEPAN",
    "tengah": "TENGAH",
    "belakang": "BELAKANG",
}

DIMENSION_LABELS = {
    "besar_kecil": "BK",
    "genap_ganjil": "GJ",
}

BK_CHOICES = ("BE", "KE")
GJ_CHOICES = ("GE", "GA")


# ─── Klasifikasi ─────────────────────────────────────────────────────────────

def classify_result(number_2d: str) -> dict:
    """
    Klasifikasi angka 2D ke kode BK dan GJ.

    Args:
        number_2d: string 2 digit, misal "95"

    Returns:
        {
            "besar_kecil":        "BE" | "KE",
            "genap_ganjil":       "GE" | "GA",
            "besar_kecil_label":  "BESAR" | "KECIL",
            "genap_ganjil_label": "GENAP" | "GANJIL",
        }
    """
    num          = int(number_2d)
    digit_second = num % 10

    bk = "BE" if num >= 50 else "KE"
    gj = "GE" if digit_second % 2 == 0 else "GA"

    return {
        "besar_kecil":        bk,
        "genap_ganjil":       gj,
        "besar_kecil_label":  CHOICE_LABELS[bk],
        "genap_ganjil_label": CHOICE_LABELS[gj],
    }


def extract_belakang(result_4d: str) -> str | None:
    """Ambil 2 digit terakhir dari hasil 4D. '1295' → '95'"""
    import re
    digits = re.sub(r"\D", "", str(result_4d).strip())
    if len(digits) < 4:
        return None
    return digits[-2:]  # 2 digit terakhir


def parse_result_full(result_4d: str) -> dict | None:
    """
    Parse hasil 4D lengkap.

    Returns:
        {
            "full":     "1295",
            "depan":    "12",
            "tengah":   "29",
            "belakang": "95",
            "belakang_bk":  "BE",
            "belakang_gj":  "GA",
        }
    """
    import re
    digits = re.sub(r"\D", "", str(result_4d).strip())
    if len(digits) < 4:
        return None
    digits = digits[-4:]

    depan    = digits[0:2]
    tengah   = digits[1:3]
    belakang = digits[2:4]
    depan_cat = classify_result(depan)
    tengah_cat = classify_result(tengah)
    belakang_cat = classify_result(belakang)

    return {
        "full":         digits,
        "depan":        depan,
        "tengah":       tengah,
        "belakang":     belakang,
        "depan_bk":     depan_cat["besar_kecil"],
        "depan_gj":     depan_cat["genap_ganjil"],
        "tengah_bk":    tengah_cat["besar_kecil"],
        "tengah_gj":    tengah_cat["genap_ganjil"],
        "belakang_bk":  belakang_cat["besar_kecil"],
        "belakang_gj":  belakang_cat["genap_ganjil"],
    }


def get_target_result(parsed: dict, target: str) -> dict:
    """
    Ambil 2D dan kategorinya berdasarkan target posisi:
      depan | tengah | belakang
    """
    if target not in ("depan", "tengah", "belakang"):
        raise ValueError(f"Target posisi tidak valid: {target}")

    return {
        "position": target,
        "number_2d": parsed[target],
        "besar_kecil": parsed[f"{target}_bk"],
        "genap_ganjil": parsed[f"{target}_gj"],
    }


def format_slot(slot: str) -> str:
    target, suffix = slot.rsplit("_", 1)
    dim = "besar_kecil" if suffix == "bk" else "genap_ganjil"
    return f"{POSITION_LABELS.get(target, target)} {DIMENSION_LABELS[dim]}"


# ─── Generator angka per kategori ─────────────────────────────────────────────

def get_numbers_for_category(choice: str) -> list[str]:
    """
    Generate 50 angka 2D yang memenuhi kategori.

    choice: "BE" | "KE" | "GE" | "GA"
    """
    if choice == "BE":
        return [str(i) for i in range(50, 100)]
    elif choice == "KE":
        return [f"{i:02d}" for i in range(0, 50)]
    elif choice == "GE":
        return [f"{i:02d}" for i in range(0, 100) if i % 2 == 0]
    elif choice == "GA":
        return [f"{i:02d}" for i in range(0, 100) if i % 2 == 1]
    else:
        raise ValueError(f"Pilihan tidak valid: {choice}. Gunakan BE/KE/GE/GA")


# ─── Helper format ────────────────────────────────────────────────────────────

def result_summary(result_4d: str) -> str:
    """Format ringkas hasil: '1295 → 2D=95 | BE (BESAR) + GA (GANJIL)'"""
    parsed = parse_result_full(result_4d)
    if not parsed:
        return result_4d
    bk = parsed["belakang_bk"]
    gj = parsed["belakang_gj"]
    return (
        f"{parsed['full']} → 2D={parsed['belakang']} | "
        f"{bk} ({CHOICE_LABELS[bk]}) + {gj} ({CHOICE_LABELS[gj]})"
    )
