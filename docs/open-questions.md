# Nyitott kérdések

## Blokkoló műszaki kérdések

1. A közvetlen, dokumentált DASNET adapter elkészült; el kell dönteni, szükséges-e
   emellett a Teledyne ISCO Universal Driver használata.
2. A pumpák COM-portját, ID-jét, A–D csatornáját és kábelezési megjegyzését a
   felhasználó adja meg az Eszközbeállításokban; az értékeket a program megőrzi.
   A célgépen a `COM1`, `COM2` és `COM4` lehetséges fizikai port, míg a `COM3`
   Intel AMT/SOL menedzsmentport. A két pumpa tényleges kiosztása még helyszíni
   azonosítást igényel.
3. Az NI fizikai csatornákat, a bemeneti bekötési módot és a bekötési/földelési
   megjegyzést a felhasználó állítja be és a program megőrzi.
   A 2026-07-15-i rendszerjelentés NI 26.x telepítést mutatott, de konkrét
   USB-6001 eszközbejegyzést nem; a DAQ nevét és felismerését ellenőrizni kell.
4. A differenciálnyomás-mérő tényleges alsó és felső tartományát a felhasználó a
   Kalibráció és biztonság ablakban adja meg.
5. A szelep 0%/100% végpontját és safe-state feszültségét a felhasználó adja meg.
   Ezek fizikai helyességéért a helyszíni validáció felel.
6. A kábelkihúzási, kommunikációvesztési és vészleállítási próba, valamint a
   felügyelt kommunikációs próba időtartama és teljesítési állapota rögzíthető az
   Eszközbeállításokban.
7. A dokumentált `STOP` parancs implementált. A két pumpa és a DAQ safe-state
   művelete egymástól függetlenül, mindenképpen megkísérlődik; a helyszíni fizikai
   beavatkozási sorrend jóváhagyása még szükséges.
8. Hogyan mérhető és fogadható el a ±1 bar szabályozási pontosság?

## Termékdöntések

- Kötelező projektmezők és fájlelnevezési séma.
- A projektek közösek, nincs projekttulajdonos vagy projektenkénti szerepkör.
- A PID-profilok implementáltan korlátlan számú, közös SQLite-adatok; minden kezelő
  kiválaszthatja, felülírhatja és törölheti őket. Ha később felhasználói jogosultsági
  modell készül, a profilok hozzáférését azzal együtt kell újraértékelni.
