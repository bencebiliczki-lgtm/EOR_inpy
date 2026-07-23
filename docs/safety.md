# Biztonsági modell

## Felügyelt pumpaindítás

A mérésindítás nem kerülheti meg a pumpák külön fizikai engedélyezését: a kezelőnek
mindkét kezdőnyomást és a két indítási térfogatáramot tartalmazó pumpaterv
megjelenítése után pontos indítási megerősítést kell megadnia. Már a bevitelkor
teljesülnie kell a tervezett köpeny–besajtoló nyomástöbbletnek. A köpenypumpa állandó áramú
nyomásfelépítési szakaszában kizárólag a minimális köpeny–besajtolás
nyomáskülönbség ellenőrzése van függőben; a besajtolópumpa ekkor még nem futhat.
Adatminőségi hiba, kapcsolatvesztés, nem véges adat vagy bármely nyomáshatár
túllépése azonnal megszakítja az indítást. A cél-nyomásnál a köpenypumpa STOP után
állandó nyomástartásra vált. A szükséges nyomástöbblet nélkül a besajtolópumpa nem
kaphat `RUN` parancsot, és annak felfutása közben a nyomástöbblet elvesztése
mindkét pumpa leállítását okozza. A mérési adatrögzítés csak mindkét kezdőnyomás
elérése után indulhat.

## Alapelv

A rendszer magas nyomáson működik, ezért a szoftver hibája nem eredményezhet korlátlan vezérlőkimenetet. A fizikai nyomáshatárok, relief megoldások és vészleállítás elsődlegesek; a szoftver kiegészítő védelmi réteg.

## Minimális interlockok

- konfigurált maximális pumpanyomás túllépése;
- differenciálnyomás `Y` határának elérése;
- szabályozási cél fölé növő nyomás konfigurált `X` eltéréssel;
- szenzorjel tartományon kívül, nem szám vagy elavult adat;
- pumpa-, NI- vagy vezérlési kommunikáció elvesztése;
- vezérlési ciklus határidejének túllépése;
- kézi vészleállítás.

## Biztonságos állapot

A pontos fizikai reakció még helyszíni kockázatelemzést igényel. Addig a kód nem feltételezheti, hogy a szelep teljes nyitása vagy zárása minden esetben biztonságos. A prototípusban a biztonságos állapot eseményt, reteszelt hibát és szimulált STOP-kérést jelent.

## Reteszelés és visszaállítás

- Vészállapot után az automatikus újraindítás tilos.
- A hibát naplózni kell a kiváltó adatokkal.
- Újraindítás csak megszűnt ok, kezelői riasztásbezárás és friss
  előfeltétel-ellenőrzés után lehetséges.
- A konfiguráció változtatása ne törölje automatikusan az aktív hibát.

A mérés szüneteltetése nem biztonsági leállítás. A PID és a mérési adatmentés
szünetel, az utolsó kimenet változatlan marad, de a teljes pumpa-, NI-,
adatminőség- és nyomásfelügyelet a vezérlési ciklus időzítésével tovább fut.
Bármely interlock a szünetből is ugyanarra a kritikus safe-state útvonalra kerül.

Kritikus hardverhibánál a safe-state után a kapcsolat megtartása tilos: minden
pumpaworkert le kell állítani és minden soros/DAQ erőforrást best-effort módon
fel kell szabadítani. A rendszer nem kapcsolódhat vissza automatikusan; konkrét
hibaüzenet és az Eszközbeállítások kezelői kapcsolatpróbája szükséges. Egy
egyszerű, mérésindítás előtti validációs hiba ezzel szemben nem indokol
portbontást.

A fizikai pumpaadapter a safe-state `STOP` kérést élvezérelten reteszeli. Egy
hibaeseményben pumpánként legfeljebb egy `STOP` kerül a soros vonalra akkor is, ha
a pumpa `PROBLEM=LOCAL MODE` választ ad vagy több felügyeleti réteg ugyanazt a
safe-state-et kéri. Új STOP csak sikeres biztonsági hibatörlés, REMOTE módba lépés
vagy új pumpafutás után engedélyezett. Ez a parancsismétlést korlátozza, a fizikai
vészleállítás és a pumpa saját védelmei továbbra is elsődlegesek.

A prototípus `SafetyMonitor` komponense reteszeli az észlelt hibákat. A retesz csak
kezelői riasztásbezárással és egy frissen beolvasott, aktuálisan biztonságos
mérési pillanatkép ismételt kiértékelése után oldható. Veszélyes friss adatnál a
bezárás elutasított, a retesz és a riasztássáv változatlan marad. A nem véges
(`NaN`, pozitív vagy negatív végtelen)
mérési érték, a kézi vészleállítás és a vezérlési határidő túllépése reteszelt
hibát vált ki.

Automata szabályozásban a kiválasztott nyomásforrás értéke külön céltúllövési
interlockot kap. A konfigurált célérték plusz a `max_control_overshoot_bar` eltérés
elérése reteszelt hibát és kimenettiltást vált ki. A dashboardon ez az eltérés
pozitív, véges barértékként állítható.

## Manuális biztonsági profil

A Developer manuális vezérlés nem készít teljes mérési pillanatképet minden
parancshoz. Pumpa-RUN előtt csak a kiválasztott, hozzáadott pumpa kapcsolatát,
véges nyomás-/áramlás-/térfogatadatát és saját maximális nyomását ellenőrzi.
A manuális szelepírás a megerősítés mellett a véges 0–100%-os tartományt és
az NI kimenet hardverengedélyét ellenőrzi. Nem hozzáadott vonali vagy
differenciálnyomás-bemenet nem generál manuális reteszt. A STOP, STOP ALL és
safe-state parancsok elsőbbsége, valamint a kapcsolatvesztés véges felismerési ideje
nem lazítható.

A vezetett funkcionális teszt csak HARDVER + READY állapotban, leállított normál
runtime, álló pumpák, aktuális sikeres kapcsolatpróba, aktív/reteszelt hiba nélküli
állapot és teljes kezelői ellenőrzőlista mellett indulhat. Megszakítás, ablakbezárás
és kivétel letiltja az új parancsokat, minden pumpán STOP-ot és SAFE AO-jelet kér.

A PID nyomásszűrése csak a szabályozási ágra hat. Az NI vonali és
differenciálnyomás kemény maximum-interlockja az EMA előtti, mediánból kalibrált
nyers nyomást értékeli, ezért a szűrés késleltetése nem takarhat el veszélyes
túllépést. A szűrt érték a PID, kijelzés, grafikon és céltúllövési felügyelet
bemenete. A túl gyakori irányváltás `VALVE_OSCILLATION`
runtime hibát és reteszelt safe-state útvonalat vált ki.

## Kötelező tesztek

Minden interlockhoz tartozzon határérték alatti, pontosan határértékű, határérték feletti, hibás adat és kapcsolatvesztési teszt. Biztonsági teszt hibája blokkolja a kiadást.
