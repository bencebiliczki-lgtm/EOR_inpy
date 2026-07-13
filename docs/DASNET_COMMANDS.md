# Teledyne ISCO D-Series / 260D DASNET parancslista

## Forrás és hatókör

Ez a lista a **Teledyne ISCO D-Series Syringe Pumps – Installation and Operation Guide** 8. fejezete, különösen a 8.8. „Serial Commands for the D Series Pump” táblázata alapján készült. A parancsok a DASNET keret `message` mezőjébe kerülnek.

A kézikönyv több D-Series típust és akár négy pumpát kezel. Az `A`, `B`, `C`, `D` utótag a vezérlő megfelelő pumpacsatlakozását jelöli. Az AFKI rendszerben használt két külön 260D vezérlőnél várhatóan mindkét pumpa saját DASNET egységazonosítóval és jellemzően `A` pumpacsatornával kezelhető; ezt a tényleges bekötésen ellenőrizni kell.

## Fontos protokollszabályok

- A parancsokat **nagybetűvel** kell küldeni.
- A szóközöket a pumpa a parancsmezőben figyelmen kívül hagyja.
- Egy DASNET üzenetben a D-Series vezérlő csak **egy parancsot** fogad el.
- Érték beállításakor mindig `=` jel szerepel a parancsban.
- A működést módosító parancsok előtt egyszer el kell küldeni a `REMOTE` parancsot.
- A `LOCAL` visszaadja a kezelést az előlapi kezelőfelületnek, és leállítja a motorokat.
- A `STOP` leállítja a pumpát, de távoli módban hagyja.
- A teljes DASNET keret formátuma nem azonos a lent felsorolt parancsszöveggel; a keret egységazonosítót, nyugtázást, forrást, hosszt, ellenőrző összeget és `CR` lezárást is tartalmaz.
- A pumpa a lekérdezésre legfeljebb körülbelül 200 ms-on belül kezdi meg a választ. Sikertelen kommunikációnál a kézikönyv három lekérdezési próbát ír le.

## Az AFKI alkalmazáshoz elsődlegesen szükséges parancsok

| Parancs | Funkció |
|---|---|
| `IDENTIFY` | Pumpatípus és firmware-verzió lekérdezése. A 260D válaszában `MODEL 260D PUMP` szerepel. |
| `REMOTE` | Távoli soros vezérlés engedélyezése; az előlapi kezelést letiltja és a motorokat leállítja. |
| `LOCAL` | Visszatérés helyi kezelésre; a motorokat leállítja. |
| `RSVP` | Kommunikáció ellenőrzése; helyes esetben `READY` válasz érkezik. |
| `CONST FLOW` | Állandó térfogatáram üzemmód. |
| `CONST PRESS` | Állandó nyomás üzemmód. |
| `FLOW=#` | Térfogatáram-célérték beállítása. |
| `PRESS=#` | Nyomáscélérték beállítása. |
| `RUN` | Pumpálás indítása. |
| `STOP` | Pumpálás leállítása, a távoli mód megtartásával. |
| `CLEAR` | Minden motor leállítása, a térfogatáram- és nyomáscélérték nullázása. |
| `REFILL` | A dugattyú alsó helyzetbe mozgatása az előre beállított utántöltési sebességgel. |
| `REFILL=#` | Utántöltési sebesség beállítása. |
| `PRESSA` | Az A pumpa aktuális nyomásának lekérdezése. |
| `FLOWA` | Az A pumpa aktuális térfogatáramának lekérdezése. |
| `VOLA` | Az A pumpahengerben maradt térfogat lekérdezése ml-ben. |
| `STATUSA` | Az A pumpa működési és hibaállapotának lekérdezése. |
| `SETPRESSA` | Az A pumpa beállított nyomáscélértékének lekérdezése. |
| `SETFLOWA` | Az A pumpa beállított térfogatáram-célértékének lekérdezése. |
| `G` | Rövid állapotcsomag: nyomás, analóg és digitális bemenetek. |
| `GG` | A `G` kibővített változata, a hatodik analóg bemenettel. |
| `G&` | Teljes állapotcsomag: nyomás, áramlás, térfogat, üzemállapot, vezérlési állapot és hibák. |
| `LIMITS` | Nyomás- és térfogatáram-határok lekérdezése. |
| `RANGEA` | Az A pumpa skálázási adatainak lekérdezése. |
| `UNITSA=BAR` | Nyomás mértékegységének barra állítása. |
| `UNITSA=ML/MIN` | Térfogatáram mértékegységének ml/min értékre állítása. |

## Teljes parancsjegyzék

### Azonosítás, kapcsolat és vezérlési mód

| Parancs | Leírás |
|---|---|
| `IDENTIFY` | Típus-, sorozat- és firmware-információ. |
| `RSVP`, `RSVPB`, `RSVPC`, `RSVPD` | Készenléti ellenőrzés; `READY` válasz. |
| `REMOTE` | Soros távoli mód engedélyezése. |
| `LOCAL` | Helyi előlapi mód engedélyezése. |
| `INDEPENDENT`, `INDEPENDENTCD` | Független pumpavezérlési mód. |
| `MODE` | Az egyes pumpák aktuális üzemmódjának lekérdezése. |

Lehetséges `MODE` jelölések: `P` állandó nyomás, `F` állandó áramlás, `R` utántöltés, `PG` nyomásgradiens, `F1` egypumpás áramlásgradiens, `F2` kétpumpás koncentrációgradiens, `CF` folyamatos állandó áramlás, `CP` folyamatos állandó nyomás, `MO` kétpumpás modifier mód, `MM` hárompumpás modifier mód.

### Indítás, leállítás és alapműveletek

| Parancs | Leírás |
|---|---|
| `RUN`, `RUNB`, `RUNC`, `RUND`, `RUNALL` | Pumpa vagy minden pumpa indítása. |
| `STOP`, `STOPB`, `STOPC`, `STOPD`, `STOPALL` | Pumpa vagy minden pumpa leállítása, távoli mód megtartásával. |
| `CLEAR` | Motorok leállítása, nyomás- és áramláscélértékek nullázása. |
| `REFILL`, `REFILLB`, `REFILLC`, `REFILLD` | Henger utántöltési mozgása. |
| `REFILL=#`, `REFILLB=#`, `REFILLC=#`, `REFILLD=#` | Utántöltési sebesség beállítása. |
| `RAPIDA`, `RAPIDB`, `RAPIDC`, `RAPIDD` | Automatikus gyors nyomásfelépítés állandó áramlás módban. |
| `DELIVER`, `DELIVERCD` | Kettős pumpamód: folyadék továbbítása. |
| `RECEIVE`, `RECEIVECD` | Kettős pumpamód: folyadék fogadása. |

### Üzemmódválasztás

| Parancs | Leírás |
|---|---|
| `CONST FLOW`, `CONST FLOWB`, `CONST FLOWC`, `CONST FLOWD` | Állandó térfogatáram mód. |
| `CONST PRESS`, `CONST PRESSB`, `CONST PRESSC`, `CONST PRESSD` | Állandó nyomás mód. |
| `CONTIN CONST FLOW`, `CONTIN CONST FLOWCD` | Folyamatos pumpálás állandó áramlással. |
| `CONTIN CONST PRESS`, `CONTIN CONST PRESSCD` | Folyamatos pumpálás állandó nyomással. |
| `MODIFIER` | Modifier-adagolási mód. |
| `CONTIN MODIFIER` | Folyamatos modifier-adagolási mód. |

### Térfogatáram

| Parancs | Leírás |
|---|---|
| `FLOW`, `FLOWCD` | Rendszer-térfogatáram lekérdezése folyamatos vagy modifier módban. |
| `FLOWA`, `FLOWB`, `FLOWC`, `FLOWD` | Tényleges pumpa-térfogatáram lekérdezése. |
| `FLOW=#`, `FLOWB=#`, `FLOWC=#`, `FLOWD=#` | Állandó áramlási célérték beállítása, `XXX.XXXXXXX ml/min` formában; öt számjegy szignifikáns. |
| `SETFLOWA`, `SETFLOWB`, `SETFLOWC`, `SETFLOWD` | Beállított térfogatáram-célérték lekérdezése. |
| `MAXFLOWA=#`, `MAXFLOWB=#`, `MAXFLOWC=#`, `MAXFLOWD=#` | Maximális térfogatáram-célérték beállítása. |
| `MAXFLOWA`, `MAXFLOWB`, `MAXFLOWC`, `MAXFLOWD` | Maximális térfogatáram-célérték lekérdezése. |
| `MINFLOWA=#`, `MINFLOWB=#`, `MINFLOWC=#`, `MINFLOWD=#` | Minimális térfogatáram-célérték beállítása. |
| `MINFLOWA`, `MINFLOWB`, `MINFLOWC`, `MINFLOWD` | Minimális térfogatáram-célérték lekérdezése. |
| `MFLOWA=#`, `MFLOWB=#`, `MFLOWC=#`, `MFLOWD=#` | Maximális áramlási korlát beállítása állandó nyomás módban. |
| `MFLOWA`, `MFLOWB`, `MFLOWC`, `MFLOWD` | Maximális áramlási korlát lekérdezése. |
| `RLIMITA`, `RLIMITB`, `RLIMITC`, `RLIMITD` | Utántöltési térfogatáram-korlát lekérdezése. |

### Nyomás

| Parancs | Leírás |
|---|---|
| `PRESS`, `PRESSCD` | Rendszernyomás lekérdezése folyamatos vagy modifier módban. |
| `PRESSA`, `PRESSB`, `PRESSC`, `PRESSD` | Pumpa tényleges nyomásának lekérdezése. |
| `PRESS=#`, `PRESSB=#`, `PRESSC=#`, `PRESSD=#` | Nyomáscélérték beállítása állandó nyomás módban. |
| `SETPRESSA`, `SETPRESSB`, `SETPRESSC`, `SETPRESSD` | Beállított nyomáscélérték lekérdezése. |
| `MAXPRESSA=#`, `MAXPRESSB=#`, `MAXPRESSC=#`, `MAXPRESSD=#` | Maximális nyomáscélérték beállítása. |
| `MAXPRESSA`, `MAXPRESSB`, `MAXPRESSC`, `MAXPRESSD` | Maximális nyomáscélérték lekérdezése. |
| `MINPRESSA=#`, `MINPRESSB=#`, `MINPRESSC=#`, `MINPRESSD=#` | Minimális nyomáscélérték beállítása. |
| `MINPRESSA`, `MINPRESSB`, `MINPRESSC`, `MINPRESSD` | Minimális nyomáscélérték lekérdezése. |
| `IPUMPA=1/0`, `IPUMPB=1/0`, `IPUMPC=1/0`, `IPUMPD=1/0` | Nyomásintegrál-szabályozás be- vagy kikapcsolása. |
| `ZEROA`, `ZEROB`, `ZEROC`, `ZEROD` | Pumpa nyomásérzékelő-offset nullázása. |

### Differenciálnyomás és külső nyomásvezérlés

| Parancs | Leírás |
|---|---|
| `PRESSCNTRLDIFF1` | Nyomásvezérlés az 1. analóg bemenetről, 50 psi tartománnyal. |
| `PRESSCNTRLDIFF1=XXXXX` | Nyomásvezérlés az 1. analóg bemenetről, 1–5000 psi/5 V tartománnyal. |
| `PRESSCNTRLDIFF2` | Nyomásvezérlés a 2. analóg bemenetről, 500 psi/5 V tartománnyal. |
| `PRESSCNTRLDIFF3` | Nyomásvezérlés a 2. analóg bemenetről, 5000 psi/5 V tartománnyal. |
| `PRESSCNTRLNORM` | Visszatérés a szabványos nyomásbemenetre. |
| `PRESSDIFF=XXXXX` | Differenciálnyomás-célérték; értéke `psi × 10`, tartománya 0–50000. |
| `PRESSDIFF` | Differenciálnyomás lekérdezése `psi × 10` formában. |
| `ZERODIFF1`, `ZERODIFF2`, `ZERODIFF3` | A megfelelő analóg nyomásbemenet offsetjének nullázása. |

### Térfogat és adagolás

| Parancs | Leírás |
|---|---|
| `VOLA`, `VOLB`, `VOLC`, `VOLD` | Hengerben maradt térfogat ml-ben. |
| `VOLTOT`, `VOLTOTCD` | Összes továbbított térfogat folyamatos vagy modifier módban. |
| `VOL RESET`, `VOL RESETCD` | Összesített térfogat nullázása. |
| `DISPENSEA`, `DISPENSEB`, `DISPENSEC`, `DISPENSED` | Beállított adagolási térfogat lekérdezése. |
| `DISPENSEA=#`, `DISPENSEB=#`, `DISPENSEC=#`, `DISPENSED=#` | Adagolási térfogat beállítása `XXX.XXX ml` formában; csak álló pumpánál. |
| `%B=#` | Modifier százalék beállítása. |

### Állapot- és konfigurációlekérdezések

| Parancs | Leírás |
|---|---|
| `G` | Nyomások, `ALOG1–ALOG5` és digitális bemenetek tömör állapotcsomagja. |
| `GG` | A `G` változata `ALOG6` értékkel. |
| `G&` | Teljes állapotcsomag egy legfeljebb hárompumpás rendszerhez. |
| `G&2` | Teljes állapotcsomag négypumpás működéshez. |
| `STATUSA`, `STATUSB`, `STATUSC`, `STATUSD` | Pumpaállapot és problémák. |
| `LIMITS`, `LIMITSB`, `LIMITSC`, `LIMITSD` | Nyomás- és térfogatáram-határok. |
| `RANGEA`, `RANGEB`, `RANGEC`, `RANGED` | Skálázási információk. |

A `STATUSx` válasz lehetséges állapotai: `STOP`, `RUN`, `REFILL`, `HOLD`, `EQUIL.`, `LOCAL`, `REMOTE`, `EXTERNAL`. Lehetséges problémák: `OVER PRESSURE`, `UNDER PRESSURE`, `CYLINDER FULL`, `CYLINDER EMPTY`, `MOTOR FAILURE`.

### Analóg és digitális I/O

| Parancs | Leírás |
|---|---|
| `ALOG1` … `ALOG6` | A megfelelő analóg bemenet nyers értékének lekérdezése. |
| `DIGITAL` | Nyolc digitális kimenet állapotának lekérdezése `H`/`L` karakterekkel. |
| `DIGITAL=xxxxxxxx` | Digitális kimenetek beállítása; `H` magas, `L` alacsony, `X` változatlan. |
| `DIG CONTROL` | Digitális kimenetek vezérlési forrásának lekérdezése. |
| `DIG CONTROL=xxxxxxxx` | Kimenetenként `R` távoli vagy `I` belső vezérlés beállítása. |

Az `ALOGx` nyers érték feszültséggé alakítása:

```text
feszültség_V = (nyers_érték - 7500) / 5000
```

A dokumentált bemeneti tartomány körülbelül −1,5–11,607 V, a felbontás 0,2 mV.

### Mértékegységek

| Parancs | Leírás |
|---|---|
| `UNITSA=<egység>` | Mértékegység beállítása minden pumpára. |

Elfogadott értékek: `ATM`, `BAR`, `KPA`, `PSI`, `ML/MIN`, `ML/HR`, `UL/MIN`, `UL/HR`.

### Gradiensprogramok

| Parancs | Leírás |
|---|---|
| `LGSL,F:xx` | Gradiensfájl kiválasztása, 01–99. |
| `LGGO` | Kiválasztott gradiensprogram indítása. |
| `LGST` | Gradiensprogram leállítása. |
| `LGE,F:xx,A:0x` | Programvégi művelet beállítása. |
| `LGDL,F:xx,S:xx` | Gradienslépés letöltése a pumpáról a PC-re. |
| `LGUL,F:xx,S:xx,...` | Gradienslépés feltöltése a PC-ről a pumpára. |

Az `LGE` műveletei: `00` végérték tartása, `01` leállítás, `02` visszatérés a kezdőértékre és tartás, `03` visszatérés és ismétlés. A gradiensparancsok a kézikönyv szerint helyi módban használhatók, és kötött mezőszélességű formátumuk miatt külön implementációt igényelnek.

## DASNET keret példa

A kézikönyvben szereplő `R304STOPD1` példa pumpa→PC irányú keret, nem a 6-os
egységnek küldött parancs:

```text
R304STOPD1[CR]
```

Mezői: `R` nyugtázás, `3` célazonosító, `04` kétjegyű hexadecimális hossz,
`STOP` üzenet és `D1` checksum.

A hálózati vezérlőtől küldött keret általános alakja:

```text
destination + acknowledgement + message_source + length + message + checksum + CR
```

A PC→pumpa keretben a célazonosító, nyugtázás és forrásazonosító külön-külön egy
karakter. Ezeket mindig két hexadecimális hosszkarakter követi. Például:

```text
6R008IDENTIFY84[CR]
││││ │       │
││││ │       └─ checksum: 84
││││ └───────── üzenet: IDENTIFY
│││└─────────── hossz: 08
││└──────────── forrásazonosító: 0
│└───────────── nyugtázás: R
└────────────── célazonosító: 6
```

A hosszmező `08`, `0F`, `10`, …, `FF` formában kódolódik; a 256 karakteres
maximális üzenethosszt `00` jelöli.

Az ellenőrző összeg két hexadecimális számjegy. Úgy kell kiszámítani, hogy a keret előző ASCII karaktereinek összegével együtt az eredmény modulo 256 értéke nulla legyen. A `CR` értéke `0x0D`, és nem része az összegnek.

## Dokumentált indítási példa

Az alábbi **parancssorrend** állandó térfogatáramú működést indít:

```text
IDENTIFY
REMOTE
CONST FLOW
FLOW=1.00
RUN
```

Biztonságos leállítás:

```text
STOP
```

Helyi kezelés visszaadása:

```text
LOCAL
```

Ezek csak a DASNET üzenetmező parancsai. A Python kommunikációs rétegnek mindegyiket a megfelelő egységazonosítóval, hosszal, ellenőrző összeggel és `CR` karakterrel ellátott keretbe kell csomagolnia.

## Megvalósítás előtt ellenőrizendő

1. Mindkét 260D vezérlő egységazonosítója.
2. Soros portonként a baud rate, adatbitek, paritás és stopbit beállítása.
3. A pumpák firmware-verziója az `IDENTIFY` paranccsal.
4. Az `A/B/C/D` pumpacsatorna tényleges kiosztása.
5. A beállított mértékegységek és az értékek formátuma.
6. A `G&` válasz pontos mezőkiosztása az adott firmware-en.
7. A kommunikációs timeout és az újrapróbálkozás működése.
8. A `STOP`, `CLEAR`, `LOCAL` és kommunikációvesztési esetek biztonságos tesztje nyomásmentes rendszerben.

## Forrás

Teledyne ISCO, *D-Series Syringe Pumps – Installation and Operation Guide*, 2022, 8. fejezet: Serial Interface, különösen 8.7.1 DASNET és 8.8 Serial Commands for the D Series Pump.
