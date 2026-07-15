# Célgép hardver- és rendszerprofilja

Ez a dokumentum a 2026. július 15. 15:39-kor készített Windows
rendszerinformációs jelentés fejlesztés szempontjából releváns kivonata. A
pillanatfelvétel nem helyettesíti az eszközök helyszíni bekötési és kommunikációs
próbáját.

## Alapplatform

| Tulajdonság | Érték |
| --- | --- |
| Gépnév | `DESKTOP-6JO8JUC` |
| Gyártó és modell | Dell OptiPlex 780 |
| Rendszertípus | x64 PC |
| Operációs rendszer | Microsoft Windows 10 Pro, 10.0.19045 build 19045 |
| Processzor | Intel Core 2 Quad Q9400, 2,66 GHz, 4 mag / 4 szál |
| Memória | 8 GB telepített RAM |
| Grafika | NVIDIA GeForce 210 |
| BIOS | Dell A01 (2009-09-01), legacy mód |
| Secure Boot | Nem támogatott |

Ez régi, AVX nélküli platform. A kiadási build nem emelheti a CPU-követelményt
AVX/AVX2 szintre, nem válthat Windows 11-et igénylő függőségre, és meg kell őriznie
a Windows 10 kompatibilitást. A Windows-csomaghoz továbbra is NumPy 1.26.4-et kell
használni a `constraints-windows-legacy.txt` szerint. Erőforrás-igényes új
funkciónál ezen a gépen kell ellenőrizni az indulási időt, a UI válaszkészségét, a
memóriahasználatot és a tartós mérés stabilitását.

## Soros portok

A jelentés készítésekor a Windows az alábbi portokat jelezte `OK` állapotúnak:

| Port | Windows-eszköz | EOR használat |
| --- | --- | --- |
| `COM1` | Kommunikációs port, ACPI `PNP0501` | Lehetséges fizikai RS-232 port; helyszínen ellenőrizendő |
| `COM2` | PCI Serial Port, `VEN_9710&DEV_9835` | Lehetséges pumpaport; helyszínen ellenőrizendő |
| `COM3` | Intel Active Management Technology – SOL | Menedzsmentport; pumpához ne legyen automatikus alapértelmezés |
| `COM4` | PCI Serial Port, `VEN_9710&DEV_9835` | Lehetséges pumpaport; helyszínen ellenőrizendő |

A két PCI soros port (`COM2`, `COM4`) ugyanahhoz a PCI Multi-IO vezérlőhöz
tartozik és IRQ 18-at használ. Ez önmagában nem hiba, de a két pumpával egyidejű,
legalább 60 perces kommunikációs próbát kell végezni. A jelentésből nem állapítható
meg, hogy melyik pumpa melyik portra van fizikailag bekötve; ezt nem szabad kódban
kitalálni vagy hallgatólagos alapértékként rögzíteni.

## National Instruments környezet

A jelentésben NI 26.x rendszerkomponensek, futó NI szolgáltatások, NI MAX és
NI-DAQmx könyvtárak láthatók. Ez alátámasztja, hogy a National Instruments
szoftverkörnyezet telepítve van.

Az `NI USB-6001` konkrét PNP-/USB-eszközbejegyzése ugyanakkor nem szerepel a
pillanatfelvételben. Emiatt a jelentés alapján nem állítható, hogy a DAQ a
jelentés készítésekor csatlakoztatva vagy NI MAX-ban felismerve volt. Hardvermód
előtt az alkalmazás felderítésével és csak olvasási kapcsolatpróbájával külön
ellenőrizni kell az eszköz nevét, az AI/AO csatornákat és a driver működését.

## Fejlesztési és kiadási következmények

- A célplatform Windows 10 x64; a kiadást ezen kell validálni.
- A teljes PyInstaller `onedir` mappát kell telepíteni, nem csak az EXE-t.
- Meg kell őrizni a régi CPU-val kompatibilis NumPy 1.26.4 korlátozást.
- A felderítés nem tekintheti az összes COM-portot pumpaportnak; a port leírását
  meg kell jeleníteni vagy a kezelői kiválasztásnál figyelembe kell venni.
- `COM3` Intel AMT/SOL port, ezért nem lehet automatikus pumpaalapérték.
- A pumpák tényleges `COM1`/`COM2`/`COM4` kiosztását helyszíni kábel- és
  kommunikációs próba dönti el.
- Az NI szoftver telepítettsége nem bizonyítja az USB-6001 csatlakozását.
- Új háttérmunka vagy grafikus funkció nem ronthatja a mérési és biztonsági
  felügyelet időzítését ezen a négymagos, 8 GB RAM-os célgépen.
