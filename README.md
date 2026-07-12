# AFKI EOR mérőrendszer

Windows alatt futó, Python-alapú mérésvezérlő és adatgyűjtő alkalmazás két Teledyne ISCO 260D pumpához, egy NI USB-6001 adatgyűjtőhöz, két nyomásmérőhöz és egy analóg vezérlésű szelephez.

> **Állapot:** kezdeti, szimulátoros projektváz. A repository jelenleg nem vezérel valódi hardvert.

## Tervezett funkciók

- két ISCO pumpa adatainak lekérése és előkészítési vezérlése;
- NI analóg bemenetek beolvasása és kalibrálása;
- szelep kézi és PID-alapú vezérlése;
- konfigurálható biztonsági határértékek és vészleállítás;
- 1 másodperc és 1 óra közötti adatrögzítési gyakoriság;
- élő, tízperces és teljes mérési diagramok;
- projektek, tetszőleges mérési szakaszok, CSV/Excel export és NAS-mentés.

## Gyorsindítás

```bash
python -m venv .venv
```

Windows PowerShell:

```powershell
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
pytest
python -m eor_control
```

A jelenlegi belépési pont szimulált mérési mintát ír ki. Valódi hardverintegráció csak a kommunikációs prototípus és a biztonsági felülvizsgálat után kerülhet be.

## Dokumentáció

A fejlesztés előtt olvasd el az `AGENTS.md` és a `docs/` fájlokat. A még nem tisztázott kérdések a `docs/open-questions.md` dokumentumban találhatók.

