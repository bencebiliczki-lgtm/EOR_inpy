AFKI EOR Controller - célgépi illesztőprogramok

Az alkalmazásmappa internetkapcsolat nélkül fut, de a fizikai hardverhez a célgépen
külön telepíteni kell:

1. A használt NI USB-6001 eszközzel kompatibilis NI-DAQmx Windows drivert.
2. A két RS-232/USB soros adapter gyártói Windows driverét, ha a Windows nem
   ismeri fel őket automatikusan.

A COM-portokat és az NI csatornákat az alkalmazás Beállítások > Eszközök ablakában
kell megadni és csak olvasási kapcsolatpróbával ellenőrizni. A csomag nem telepít
drivert, és nem aktivál automatikusan fizikai hardvert.
