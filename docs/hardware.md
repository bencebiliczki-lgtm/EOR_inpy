# Hardver és kommunikáció

## Teledyne ISCO 260D pumpák

- Darabszám: 2.
- Szerepek: köpenypumpa és besajtoló pumpa.
- Fizikai kapcsolat: RS-232 soros port, külön COM-portokon.
- Gyári alap soros beállítás a kézikönyv szerint: 9600 bit/s, 8 adatbit, nincs paritás, 1 stopbit.
- A pumpa távoli vezérléséhez remote mód szükséges.
- A kézikönyv DASNET protokollt, Universal Drivert és soros parancskészletet dokumentál.
- Releváns státuszparancsok között szerepel a `G`, `GG` és `G&`, de a teljes DASNET-keretezést implementáció előtt ellenőrizni kell.
- Kapcsolathoz a gyártó null-modem kábele vagy azzal egyenértékű bekötés szükséges.

### Implementált adapter

A `dasnet.py` a PC→pumpa és pumpa→PC kereteket, a modulo-256 checksumot, a `CR`
lezárást, az üres hálózati indító keretet és a háromszori újrapróbálkozást kezeli.
A `isco.py` adapter `RSVP` és `IDENTIFY` segítségével ellenőrzi a kapcsolatot és a
260D modellt, majd külön `PRESSx`, `FLOWx`, `VOLx` és `STATUSx` lekérdezésekből
állítja össze a pumpaállapotot. A dokumentált `REMOTE`, `CONST FLOW`, `FLOW=#`,
`CONST PRESS`, `PRESS=#`, `RUN`, `STOP`, `CLEAR` és `LOCAL` műveletek elérhetők.

A kapcsolat létrehozása önmagában nem küld `REMOTE` vagy motorindító parancsot.
A tényleges használathoz konfigurálni kell a COM-portot, a 0–9 egységazonosítót,
az A–D pumpacsatornát, a baud rate-et és az aktuális pumpamértékegységeket.
Ezeket, valamint a kábelezési megjegyzést a felhasználó az Eszközbeállításokban
adja meg; a program a beállításokat megőrzi.

## NI USB-6001

A konfiguráció két analóg nyomásbemenetet kezel: vonali és differenciálnyomást.
A vonali nyomás egyben a berendezés belépő nyomása, ezért nem tartozik hozzá külön
harmadik NI-csatorna. A fizikai csatornaneveket a felhasználó adja meg az
Eszközbeállításokban; a program megőrzi és a csak olvasási kapcsolatpróbán
ellenőrzi őket. Mindkét csatorna külön kalibrálható; ismert kiinduló
jeltartományuk 1–5 V, a vonali érzékelő alapértelmezett leképezése 0–400 bar.

- USB-kapcsolat a Windows géppel.
- Analóg bemenet: vonali nyomásmérő.
- Analóg bemenet: Siemens differenciálnyomás-mérő.
- Analóg kimenet: HANBAY MCJ-050AF szelep vezérlése.
- A beállított csatornák nem lehetnek üresek vagy azonosak. A bekötést, földelést,
  bemeneti módot és mintavételi korlátokat a hardverdokumentációban kell rögzíteni.
  A felhasználó `DEFAULT`, `RSE`, `NRSE`, differenciális vagy
  pszeudodifferenciális bemeneti módot választhat, és külön bekötési/földelési
  megjegyzést menthet.

### Implementált adapter

A `ni.py` logikai `line_pressure`, `differential_pressure` és `valve_output`
csatornákat köt konfigurált NI fizikai csatornákhoz. A fizikai analóg kimenet külön,
pontos megerősítő szöveg nélkül tiltott. A safe-state feszültséget és az engedélyezett
kimeneti tartományt kötelező konfigurálni; a kód nem választ helyettük alapértelmezett
fizikai biztonsági állapotot. Az `AnalogValveActuator` explicit 0%/100%
kalibrációból számít feszültséget, így a fordított szelepkarakterisztika is kezelhető.

## Szenzorok

- Vonali nyomásmérő: 1–5 V, jelenlegi információ szerint 0–400 bar.
- Differenciálnyomás-mérő: Siemens 7MF4533-1HA32-2AB6-Z, 1–5 V; a konfigurált mérési tartomány változhat.
- Lineáris kétpontos átszámítás: `y = y_min + (U-U_min)*(y_max-y_min)/(U_max-U_min)`.
- A tartományon kívüli feszültséget ne korlátozzuk csendben: adatminőségi hibaként kell jelezni.

## Szelep

- Típus: HANBAY MCJ-050AF.
- Vezérlőjel: 1–5 V.
- A feszültség és a tényleges nyitottság/fordulatszám kapcsolatát kalibrálni kell.
- A biztonságos kimeneti feszültséget és a kapcsolatvesztéskori mechanikai állapotot helyszíni teszt dönti el.

Az Eszközbeállításokban a felhasználó rögzíti a szelep 0%/100% végpontjait és a
safe-state feszültséget. Ugyanitt megadható a felügyelt kommunikációs próba előírt
időtartama, valamint megjelölhető a kábelkihúzási, vészleállítási és felügyelt próba
sikeres teljesítése. A jelölések dokumentációs adatok; önmagukban nem helyettesítik
a fizikai tesztet és nem kapcsolnak kimenetet.
