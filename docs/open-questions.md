# Nyitott kérdések

## Blokkoló műszaki kérdések

1. A közvetlen, dokumentált DASNET adapter elkészült; el kell dönteni, szükséges-e
   emellett a Teledyne ISCO Universal Driver használata.
2. A pumpák COM-portját, ID-jét, A–D csatornáját és kábelezési megjegyzését a
   felhasználó adja meg az Eszközbeállításokban; az értékeket a program megőrzi.
3. Az NI fizikai csatornákat, a bemeneti bekötési módot és a bekötési/földelési
   megjegyzést a felhasználó állítja be és a program megőrzi.
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
- PID profilok száma és hozzáférése.
