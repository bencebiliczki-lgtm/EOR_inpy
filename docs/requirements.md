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
- belépő nyomás;
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
- A szakasz típusa, folyadéka/vegyszere, cél nyomása, cél térfogatárama és
  megjegyzése szerkeszthető; a szakaszok rendezhetők és törölhetők.
- Példák: hidegvizes, melegvizes, olajkiszorításos és különböző vegyszeres szakaszok.
- A projekt neve jelenjen meg az exportált fájl nevében.
- Javasolt NAS-struktúra: `EOR mérés/<év>/<projekt neve>/`.

## Megjelenítés

- Kapcsolati állapot minden eszközhöz.
- Aktuális értékek és aktív riasztások jól láthatóan.
- Az elmúlt 10 perc élő diagramja.
- A teljes rögzített mérés külön diagramja.
- Választható adatsorok és szabadon skálázható tengelyek.
- A szelep aktuális állásának folyamatos kijelzése.
- A belépő nyomás külön csatornaként jelenjen meg és PID-forrásként választható legyen.

## Kalibráció

A nyomásmérőkhöz megadható legalább két kalibrációs pont: alsó/felső feszültség és a hozzájuk tartozó fizikai érték. A konfiguráció legyen verziózott, és a mérés indulásakor készüljön róla pillanatkép.

## Vezérlés

- Külön előkészítési nézet a pumpák felügyelt vezérlésére.
- Automatikus nyomásfelépítés közben a köpeny- és besajtolási nyomás megengedett különbségének betartása.
- A szelep automata és kézi módban működhet.
- Automata módban a szabályozási forrás választható legyen: besajtoló pumpa nyomása vagy vonali nyomásmérő.
- A PID paraméterei módosíthatók és névvel menthető profilokba rendezhetők.
- Cél a beállított nyomás ±1 bar tartása, ennek igazolási módszerét még rögzíteni kell.

## Adatmentés

- A nyers mérési adatok folyamatos, összeomlástűrő helyi mentése kötelező.
- A NAS-ra írás ne blokkolja az adatgyűjtést; hálózati hiba esetén helyi várólista szükséges.
- A nyers és felhasználói magyar CSV pontosvesszős, tizedesvesszős formátumot használ;
  a felhasználói exportnál más elválasztó és tizedespont is választható.
- Az export nem helyettesíti a belső nyers adatforrást.
