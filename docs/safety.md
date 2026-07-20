# Biztonsági modell

## Alapelv

A rendszer magas nyomáson működik, ezért a szoftver hibája nem eredményezhet korlátlan vezérlőkimenetet. A fizikai nyomáshatárok, relief megoldások és vészleállítás elsődlegesek; a szoftver kiegészítő védelmi réteg.

## Minimális interlockok

- konfigurált maximális pumpanyomás túllépése;
- differenciálnyomás `Y` határának elérése;
- szabályozási cél fölé növő nyomás konfigurált `X` eltéréssel;
- köpeny- és besajtolási nyomás közötti legalább 20 baros különbség elvesztése;
- szenzorjel tartományon kívül, nem szám vagy elavult adat;
- pumpa-, NI- vagy vezérlési kommunikáció elvesztése;
- vezérlési ciklus határidejének túllépése;
- kézi vészleállítás.

## Biztonságos állapot

A pontos fizikai reakció még helyszíni kockázatelemzést igényel. Addig a kód nem feltételezheti, hogy a szelep teljes nyitása vagy zárása minden esetben biztonságos. A prototípusban a biztonságos állapot eseményt, reteszelt hibát és szimulált STOP-kérést jelent.

## Reteszelés és visszaállítás

- Vészállapot után az automatikus újraindítás tilos.
- A hibát naplózni kell a kiváltó adatokkal.
- Újraindítás csak megszűnt ok, kezelői nyugtázás és előfeltétel-ellenőrzés után lehetséges.
- A konfiguráció változtatása ne törölje automatikusan az aktív hibát.

A prototípus `SafetyMonitor` komponense reteszeli az észlelt hibákat. A retesz csak
kezelői nyugtázással és egy aktuálisan biztonságos mérési pillanatkép ismételt
kiértékelése után oldható. A nem véges (`NaN`, pozitív vagy negatív végtelen)
mérési érték, a kézi vészleállítás és a vezérlési határidő túllépése reteszelt
hibát vált ki.

Automata szabályozásban a kiválasztott nyomásforrás értéke külön céltúllövési
interlockot kap. A konfigurált célérték plusz a `max_control_overshoot_bar` eltérés
elérése reteszelt hibát és kimenettiltást vált ki. A dashboardon ez az eltérés
pozitív, véges barértékként állítható.

Fizikai pumpaindítás csak HARDVER + READY állapotban, korábbi kapcsolatpróba és
hardverengedély után lehetséges. A köpenypumpa indításához `RUN JACKET PUMP`, a
besajtolópumpához `RUN INJECTION PUMP` pontos kezelői megerősítés szükséges. A
besajtolás 20 bar alatti pillanatnyi köpenytöbbletnél szoftveresen blokkolt.
A mérés indítása előtt egy nem mentett, teljes biztonsági kiértékelésű minta készül.
Kalibrációs tartományhiba vagy aktív interlock esetén a futtatószál nem indul el,
az alkalmazás biztonságos kimeneti állapotot kér, és a konkrét okot megjeleníti.

A vezetett funkcionális teszt csak HARDVER + READY állapotban, leállított normál
runtime, álló pumpák, aktuális sikeres kapcsolatpróba, aktív/reteszelt hiba nélküli
állapot és teljes kezelői ellenőrzőlista mellett indulhat. Megszakítás, ablakbezárás
és kivétel letiltja az új parancsokat, minden pumpán STOP-ot és SAFE AO-jelet kér.

A PID nyomásszűrése csak a szabályozási ágra hat; minden interlock a szűretlen
mérési pillanatképet értékeli. A túl gyakori irányváltás `VALVE_OSCILLATION`
runtime hibát és reteszelt safe-state útvonalat vált ki.

## Kötelező tesztek

Minden interlockhoz tartozzon határérték alatti, pontosan határértékű, határérték feletti, hibás adat és kapcsolatvesztési teszt. Biztonsági teszt hibája blokkolja a kiadást.
