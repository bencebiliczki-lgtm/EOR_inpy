# Funkcionális követelmények

## Rendszercél

A rendszer egy EOR kőzetminta-vizsgálat során gyűjti, megjeleníti és menti a pumpák, nyomásmérők és a szabályozószelep adatait, valamint biztonsági felügyelet mellett támogatja az eszközök vezérlését.

## Mért és rögzített adatok

Minden mintavételi időpontban rögzítendő:

- köpenypumpa nyomása, térfogatárama és maradék cilindertérfogata;
- besajtoló pumpa nyomása, térfogatárama és maradék cilindertérfogata;
- a mérés kezdete óta besajtolt térfogat;
- vonali nyomás;
- differenciálnyomás;
- szelepvezérlés százalékos értéke;
- aktív mérési szakasz;
- rendszerállapot, adatminőség és aktív hibák.

A mintavételi gyakoriság 1 másodperc és 1 óra között konfigurálható. A vezérlési ciklus gyakorisága ettől független, gyorsabb belső beállítás legyen.

## Mérési projekt

- Egy mérés egy visszanyitható projekt.
- A projekt tartalmazza a nevét, létrehozási idejét, megjegyzéseit, konfigurációját
  és kalibrációs pillanatképét. A projektek közösek: nincs tulajdonosuk, minden
  kezelő dolgozhat minden mérésen.
- Egy projekt tetszőleges számú, hozzáadható és átnevezhető szakaszt tartalmazhat.
- A projektválasztó képernyő listázza a korábbi projekteket és projektenként az
  utoljára használt mérési fázist, valamint új projekt létrehozását is biztosítja.
- Az „Aktív projekt” dashboard-kártya dropdownban listázza az aktív projekt mérési
  szakaszait, és közvetlenül lehetővé teszi az aktív szakasz váltását.
- A szakasz-dropdown utolsó eleme mindig **„+ Új szakasz hozzáadása…”** legyen.
  Kiválasztása külön ablakot nyisson név/típus, folyadék, célértékek és megjegyzés
  megadásához; megszakításkor az előző aktív szakasz maradjon kiválasztva.
- Ha nincs érvényes utoljára megnyitott projekt, az alkalmazás indításkor
  automatikusan megjeleníti a projektválasztót, és nem választ projektet önkényesen.
- Projekt a projektválasztóból és a projektkezelőből is törölhető legyen, kötelező
  megerősítéssel. A törlés távolítsa el a projektet és fázis-metaadatait, valamint az
  utolsó kiválasztás érvénytelenné vált hivatkozásait, de ne törölje automatikusan a
  nyers mérési CSV-ket.
- A szakasz neve egyben a típusa. Emellett a folyadék/vegyszer, cél nyomás, cél
  térfogatáram és megjegyzés szerkeszthető; a szakaszok rendezhetők és törölhetők.
- Példák: hidegvizes, melegvizes, olajkiszorításos és különböző vegyszeres szakaszok.
- A projekt és a mérési fázis neve jelenjen meg az exportált fájl nevében.
- Javasolt NAS-struktúra: `EOR mérés/<év>/<projekt neve>/`.

## Megjelenítés

- Minden szöveges kijelző és szerkeszthető szövegmező háttere legyen átlátszó az
  egész alkalmazásban, világos, sötét és rendszer-témában is. Riasztási banner vagy
  színes riasztási widget ne jelenjen meg: minden kezelői riasztás Windows
  rendszerértesítésként jelenjen meg, háttérben vagy minimalizálva pedig a
  tálcagomb is kérjen figyelmet.
- A bal és jobb oldalsáv splitterrel akkor is átméretezhető maradjon, amikor a
  tartalma miatt függőleges scrollbar jelenik meg.
- A jobb oldali vezérlőpanel minden inputmezője azonos szélességű legyen az
  oldalsáv bármely szélességén; hosszabb címke nem törheti a hozzá tartozó mezőt
  teljes szélességű külön sorba.
- Kapcsolati állapot minden eszközhöz.
- Aktuális értékek és aktív riasztások jól láthatóan.
- Az elmúlt 10 perc élő diagramja.
- A besajtolási térfogatáram külön, `ml/h` egységű élő diagramja.
- A teljes rögzített mérés a dashboard középső területének külön füle legyen; ne
  külön felugró ablakban jelenjen meg.
- A teljes mérés diagramja legyen mérési fázisra szűrhető, és külön idővonalon
  mutassa a fázisváltásokat, az ismételt fázisszakaszokat is elkülönítve.
- Választható adatsorok és szabadon skálázható tengelyek.
- A szelep aktuális állásának folyamatos kijelzése.
- Mindkét pumpánál jelenjen meg a mérés indítása óta számított, előjeles nettó
  térfogatváltozás; a negatív értéket a felület nem rejtheti el.
- Külön, görgethető mérési áttekintő ablak jelenítse meg részletesen az aktív
  projektet és fázist, az eszközkapcsolatokat, az összes élő mérési értéket, a
  szelepjelet, a kalibrációkat és a biztonsági határértékeket.
- A vonali nyomás egyben a berendezés belépő nyomása; nem kezelhető külön,
  duplikált érzékelőként vagy adatsorként.

## Kalibráció

A nyomásmérőkhöz megadható legalább két kalibrációs pont: alsó/felső feszültség és
a hozzájuk tartozó fizikai érték. A kalibráció és a biztonsági határértékek külön,
áttekinthető felugró ablakban szerkeszthetők; futó mérés közben nem módosíthatók.
A konfiguráció legyen verziózott, és a mérés indulásakor készüljön róla pillanatkép.

## Vezérlés

- Az alkalmazás ugyanabból a onefile EXE-ből `terminal` argumentummal interaktív,
  állapotot megőrző parancssori vezérlést biztosítson.
- A terminálból elérhető legyen a státusz, csatlakozás, mérésindítás/-leállítás,
  vészleállítás, hibanyugtázás, leválasztás, kézi/automata szabályozás, mérési
  szakasz és adatrögzítési időköz beállítása.
- A terminál mód alapértelmezetten és jelen kiadásban kizárólag szimulációt
  vezérelhet; nem írhat CSV-t, nem hozhat létre NAS-feladatot és nem érhet el
  fizikai kimenetet. A hardveres terminálvezérlés csak külön, később jóváhagyott,
  a felderítést és explicit kezelői megerősítést megtartó terv alapján engedhető.
- Külön előkészítési nézet a pumpák felügyelt vezérlésére.
- Automatikus nyomásfelépítés közben a köpeny- és besajtolási nyomás megengedett különbségének betartása.
- A szelep automata és kézi módban működhet.
- Automata módban a szabályozási forrás választható legyen: besajtoló pumpa nyomása vagy vonali nyomásmérő.
- A PID paraméterei módosíthatók és névvel menthető profilokba rendezhetők.
- Cél a beállított nyomás ±1 bar tartása, ennek igazolási módszerét még rögzíteni kell.

## Adatmentés

- A nyers mérési adatok folyamatos, összeomlástűrő helyi mentése kötelező.
- Nyers mérési adat kizárólag explicit, megerősített hardvermódban menthető. A
  szimuláció nem hozhat létre vagy módosíthat CSV-t, projekt-pillanatképet vagy
  NAS-szinkronfeladatot, még akkor sem, ha a runtime perzisztálást kérne.
- A **SZIMULÁCIÓ — NINCS ADATMENTÉS** és az **ÉLES MÉRÉS — ADATMENTÉS AKTÍV**
  üzemmódváltást eseményalapú értesítés jelezze; állandó dashboard-módsáv ne
  foglaljon helyet.
- A biztonsági hibák állandó dashboard-sáv helyett nem blokkoló rendszerértesítést
  adjanak. Minimalizált vagy háttérben lévő alkalmazásnál Windows tálcaértesítés és
  tálcagomb-figyelmeztetés szükséges; azonos aktív hiba ciklusonként ne ismétlődjön.
- Developer módban külön **Szimulációs mód** kapcsoló legyen. Az átváltás csak
  leválasztott, IDLE állapotban történhet; a szimulációs runtime fizikai kimenetet
  és mérési perzisztenciát nem használhat.
- Az éles nyers fájl neve `_live_raw.csv` végződést kapjon; automatikus
  előzménybetöltés és NAS-szinkron csak ilyen, egyértelműen jelölt fájlt használhat.
- Minden mérési fázis külön nyers CSV-fájlba kerüljön; fázisváltás nem írhatja az
  új fázis rekordjait az előző fázis fájljába.
- A teljes mérés nézet a külön fázisfájlokat csak megjelenítéskor egyesítheti
  memóriában. Összesített nyers CSV vagy többfázisú Excel-export nem készülhet.
- A felhasználói CSV- és Excel-export mindig egy kiválasztott mérési fázisra
  vonatkozzon.
- A NAS-ra írás ne blokkolja az adatgyűjtést; hálózati hiba esetén helyi várólista szükséges.
- A nyers és felhasználói magyar CSV pontosvesszős, tizedesvesszős formátumot használ;
  a felhasználói exportnál más elválasztó és tizedespont is választható.
- Az export nem helyettesíti a belső nyers adatforrást.
