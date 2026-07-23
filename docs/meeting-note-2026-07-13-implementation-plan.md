# A 2026-07-13-i meeting note megvalósítási terve

Forrás: `G:\My Drive\Jegyzetek\AFI (EOR)\meeting note 2026-07-13.md`

## Cél

A mérési előzmények legyenek projektenként és mérési fázisonként áttekinthetők,
a teljes mérés diagramja mutassa a fázishatárokat, továbbá mindkét pumpánál
előjelesen legyen követhető az indítás óta bekövetkezett nettó térfogatváltozás.

## Rögzített értelmezések

- Az „indítás óta” a **Mérés indítása** műveletet jelenti, nem az alkalmazás
  elindítását.
- A nettó térfogatváltozás képlete:
  `indításkori maradék térfogat - aktuális maradék térfogat`.
- Pozitív érték esetén folyadék távozott a pumpából; negatív érték esetén a
  pumpa maradék térfogata az indításkori érték fölé nőtt.
- A fázis „haladása” első körben időbeli lefutást és fázishatárokat jelent.
  Százalékos készültséghez később tervezett időtartam vagy céltérfogat szükséges.
- A régi nyers CSV-k változatlanul megőrzendők és megnyithatók maradnak.
- Minden mérési fázis külön nyers CSV-fájlt kap. Összesített nyers CSV nem
  készül; a projekt Excel-munkafüzete a lezárt fázisokat külön munkalapokon
  tartalmazza.
- A **Teljes mérés** nézet a különálló fázis-CSV-ket kizárólag beolvasáskor,
  memóriában rendezi közös időrendbe.

## Hiánylista és állapot

| Terület | Kiinduló állapot | Célállapot | Státusz |
|---|---|---|---|
| Élő 10 perces diagram | Elkészült | Megőrzés | Kész |
| Teljes mérés diagram | Elkészült | Megőrzés | Kész |
| Fázisszűrés | Nincs | Összes vagy egy kiválasztott fázis | Elkészült |
| Fázis-idővonal | Nincs | Színes, ismétlődő szakaszokat is elkülönítő idővonal | Elkészült |
| Besajtolt térfogat | Nemnegatívra korlátozott | Előjeles nettó változás | Elkészült |
| Köpenytérfogat | Nincs számláló | Előjeles nettó változás | Elkészült |
| CSV-formátum | Régi térfogatmező | V2 mezők és V1 kompatibilitás | Elkészült |
| Fázisonkénti nyers fájl | Projektenként egy CSV | Minden fázishoz külön CSV | Elkészült |
| Teljes mérés adatforrása | Egy projekt-CSV | Fázis-CSV-k memóriabeli összefűzése | Elkészült |
| Felhasználói export | Projektfájl exportja | Fázis-CSV és projekt-Excel külön fázislapokkal | Elkészült |
| UI-szövegek | „Mérés óta…” | „Indítás óta nettó…” | Elkészült |

## Megvalósítás állapota

Az első fejlesztési csomag elkészült. A teljes mérés ablak fázisra szűrhető és
fázis-idővonalat rajzol. Mindkét pumpa nettó térfogatváltozása előjelesen kerül a
mérési rekordba és a V2 CSV-be. A V1 fájl írásra történő megnyitásakor
`_v1_backup.csv` biztonsági másolat készül, majd a munkafájl atomikusan V2
formátumra frissül. A nyers adatrögzítés projekten és fázison belül külön CSV-be
történik. A teljes mérés nézet ezeket csak memóriában egyesíti. A CSV-export az
aktív fázisra korlátozódik, a projekt Excel-fájlja pedig a lezárt fázisokat külön
munkalapokon tartja.

## Megvalósítási lépések

### 1. Tesztek

- Fázisnevek kigyűjtése a nyers mérésből.
- Egy kiválasztott fázis mintáinak szűrése.
- Ismétlődő fázissorrend kezelése: `víz → olaj → víz`.
- Mindkét pumpa pozitív és negatív nettó térfogatváltozása.
- Számlálók újraindítása a **Mérés indítása** műveletkor.
- Régi és új CSV megnyitása, valamint új CSV- és Excel-export.

### 2. Mérési modell és adattárolás

- A `MeasurementService` mindkét pumpa indításkori maradék térfogatát tárolja.
- A rekord tartalmazza a köpeny- és besajtolópumpa előjeles nettó változását.
- A V2 CSV egyértelmű nevű nettó térfogatoszlopokat használ.
- A beolvasó a V1 CSV-t kanonikus V2 táblává alakítja; a régi fájlt nem írja át.
- A nyers mérési fájl neve a projektet és a fázist is azonosítja:
  `<projekt>_<fázis>_live_raw.csv`.
- Fázisváltáskor az író lezárja az előző fázis fájlját, és a következő rekordot a
  kiválasztott fázis saját CSV-jébe írja.

### 3. Teljes mérés diagram

- Fázisválasztó: **Összes mérési fázis** és a projekt külön fázisfájljaiban
  található fázisok.
- A külön fájlok rekordjai csak a diagram betöltésekor kerülnek közös időrendbe.
- A szűrés a mintákat és az időpontokat együtt kezeli.
- A státuszsor megmutatja a fázis mintaszámát és időtartamát.
- Az összes fázis nézet színes idővonalon jelöli a folytonos fázisszakaszokat.

### 4. UI és dokumentáció

- „Indítás óta nettó besajtolt térfogat”.
- „Indítás óta nettó köpenytérfogat”.
- Negatív értékhez magyarázó tooltip.
- Követelmények, architektúra és tesztstratégia frissítése.

## Elfogadási feltételek

- Régi mérési CSV adatvesztés nélkül megnyílik.
- Egy mérési fázis rekordjai csak az adott fázis nyers CSV-jébe kerülnek.
- Fázisváltás után új fázis-CSV jön létre, a korábbi fájl változatlanul megmarad.
- A CSV-export kizárólag a kiválasztott fázis adatait tartalmazza; a projekt
  Excel-fájljában minden lezárt fázis külön munkalapon szerepel.
- A teljes mérés nézet több fázis-CSV-t meg tud jeleníteni, de nem hoz létre
  összesített nyers CSV-t.
- Egy korábbi fázis önállóan visszanézhető.
- A teljes nézetben minden fázis és minden fázisváltás látható.
- A nettó térfogat negatív lehet, és ezt a UI nem rejti el.
- Új mérés indításakor mindkét nettó számláló nulláról indul.
- `ruff check .`, `mypy src` és `pytest` sikeresen lefut.

## Nyitott továbbfejlesztés

Százalékos fáziskészültséghez el kell dönteni, hogy a nevező tervezett időtartam,
célzott nettó térfogat vagy más technológiai végfeltétel legyen. Ezt a jelen terv
nem találja ki; a gyártói és technológiai követelmény alapján kell rögzíteni.
