AI MEKDEP — DOLY IŞLEÝÄN WARIANT
================================

Başlatmak:
1) start_ai_mekdep.bat faýlyna iki gezek basyň.
2) OpenAI API key soralsa, öz API açaryňyzy giriziň.
3) Brauzerde http://127.0.0.1:5000 açyň.

Baş sahypadaky 6 bölüm:
1. Okuwçy — synp > ders > tema > giňişleýin AI düşündiriş > sorag-jogap > test.
2. Mugallym — tema mazmunyny redaktirlemek, test goşmak, soňky netijeleri görmek.
3. Dersler — 5–12-nji synplaryň girizilen ähli derslerini we maksatnamalaryny açmak.
4. Testler — synp/ders boýunça tema saýlamak, bar testleri çözmek, test ýok bolsa OpenAI arkaly 5 test döretmek.
5. AI kömekçi — synp, ders, tema saýlap erkin okuw soragyny bermek.
6. Netijeler — okuwçynyň ady boýunça test netijelerini, ortaça we iň gowy netijäni görmek.

Mugallym paneli:
- Başlangyç parol: 1234
- Üýtgetmek üçin Windows-da AI_MEKDEP_TEACHER_PASSWORD gurşaw üýtgeýjisini belläp bolýar.

OpenAI:
- Başlangyç model: gpt-5.6-luna (çykdajysy pes bolmagy üçin).
- Başga modeli OPENAI_MODEL gurşaw üýtgeýjisi bilen saýlap bolýar.
- API ulanyşy ChatGPT Plus-dan aýratyn töleglidir.

Maglumat bazasy:
- SQLite: ai_mekdep.db
- Test netijeleri test_results tablisasynda saklanýar.
- Maksatnama sapaklarynyň sany degişli ders sagatlaryna laýyklykda saklanýar.

Bellik:
Bu lokal mekdep prototipidir. Internet/OpenAI ýok wagty maksatnama we mugallymyň bazada saklan mazmuny işleýär, AI düşündiriş/test döretmek üçin OpenAI API gerek.
