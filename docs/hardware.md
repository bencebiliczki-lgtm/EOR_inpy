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

## NI USB-6001

- USB-kapcsolat a Windows géppel.
- Analóg bemenet: vonali nyomásmérő.
- Analóg bemenet: Siemens differenciálnyomás-mérő.
- Analóg kimenet: HANBAY MCJ-050AF szelep vezérlése.
- A végleges csatornaszámokat, bekötést, földelést, bemeneti módot és mintavételi korlátokat a helyszínen kell rögzíteni.

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

