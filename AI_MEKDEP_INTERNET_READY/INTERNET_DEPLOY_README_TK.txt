AI MEKDEP — Internetde işletmek üçin taýýar paket

1) Bu paket Render + OpenAI üçin taýýarlanyldy.
2) Render-de durnukly ulanmak üçin persistent disk zerur, sebäbi AI MEKDEP häzirki wagtda SQLite we ýerli kitap faýllaryny ulanýar.
3) render.yaml 20 GB persistent disk bilen taýýarlandy. Gerek bolsa soň ulaldyp bolýar.
4) OPENAI_API_KEY diňe Render Environment/Secret hökmünde girizilýär. Kodyň içine ýazmaň.
5) OPENAI_MODEL = gpt-5.6-luna — okuwçy soraglary üçin çykdajysy pes model.

DEPLOY GURLUŞY
GitHub -> Render Web Service -> /var/data persistent disk
                           -> SQLite database
                           -> uploaded books
                           -> OpenAI API

RENDER ÜÇIN
- Build: pip install -r requirements.txt
- Start: gunicorn --workers 2 --threads 4 --timeout 120 --bind 0.0.0.0:$PORT app:app
- Health: /health
- Persistent path: /var/data

MÖHÜM
Render Free web service persistent disk goldamaýar. Free režim diňe synag üçin ulanylsa, restart/redeploy wagtynda täze maglumatlar ýitip biler. Mekdepde hakyky ulanmak üçin persistent diskli plan gerek.
