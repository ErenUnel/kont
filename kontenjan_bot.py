"""
İTÜ OBS kontenjan takip botu (GitHub Actions uyumlu)
- Belirlenen CRN'lerin kontenjanını ve yazılan sayısını dakikada bir kontrol eder
- Kontenjan artarsa veya boş yer açılırsa:
  * oto_kayit aktifse → OBS'ye giriş yapıp dersi otomatik kaydeder
  * değilse → sadece bildirim gönderir (ntfy veya Telegram)

Gizli bilgiler ortam değişkeninden okunur:
  NTFY_KONU, NTFY_KONU_SNT, NTFY_KONU_ATA  -> ntfy konu adları
  TELEGRAM_TOKEN, TELEGRAM_CHAT_ID           -> Telegram için
  OBS_KULLANICI, OBS_SIFRE                   -> otomatik kayıt için
"""

import os
import random
import time
from datetime import datetime, timedelta, timezone

import requests
from bs4 import BeautifulSoup

from ders_kayit import kayit_dene

# ================== AYARLAR ==================
TEMEL_URL = "https://obs.itu.edu.tr/public/DersProgram/DersProgramSearch?programSeviyeTipiAnahtari=LS&dersBransKoduId="

# Taranacak ders listeleri (URL'nin sonundaki numara ders kodunu belirler)
DERS_URLLERI = [
    TEMEL_URL + "3",              # BLG
]

# ============ TAKİP KONFİGÜRASYONU ============
# Her CRN için: ntfy konusu ve otomatik kayıt açık/kapalı
# oto_kayit: True  → kontenjan açılınca otomatik kayıt dene
# oto_kayit: False → sadece bildirim gönder
TAKIP_CONFIG = {
    "12575": {"ntfy": os.getenv("NTFY_KONU", ""),  "oto_kayit": True},   # Computer Vision
}

TAKIP_EDILEN_CRNLER = list(TAKIP_CONFIG)

KONTROL_ARALIGI_SN = 60  # 60 sn'nin altına inme

# Kaç dakika sonra kapansın (GitHub Actions için). 0 = sonsuza kadar çalış
CALISMA_SURESI_DK = int(os.getenv("CALISMA_SURESI_DK", "0"))

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

# OBS giriş bilgileri (otomatik kayıt için)
OBS_KULLANICI = os.getenv("OBS_KULLANICI", "")
OBS_SIFRE = os.getenv("OBS_SIFRE", "")
# =============================================

HEADERS = {
    "User-Agent": "Mozilla/5.0 (kontenjan-takip, kisisel kullanim)",
    "X-Requested-With": "XMLHttpRequest",
    "Referer": "https://obs.itu.edu.tr/public/DersProgram",
}
TR_SAATI = timezone(timedelta(hours=3))

# Başarıyla kaydedilen CRN'leri takip et (tekrar denememek için)
KAYDEDILEN_CRNLER = set()


def bildirim_gonder(mesaj: str, crn: str = "") -> None:
    """crn verilirse sadece o dersin konusuna, verilmezse tum konulara gonderir."""
    print(f"[BİLDİRİM] {mesaj}")

    if crn:
        konular = [TAKIP_CONFIG.get(crn, {}).get("ntfy", "")]
    else:
        konular = list(dict.fromkeys(
            cfg["ntfy"] for cfg in TAKIP_CONFIG.values()
        ))

    try:
        if TELEGRAM_TOKEN and TELEGRAM_CHAT_ID:
            requests.post(
                f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
                data={"chat_id": TELEGRAM_CHAT_ID, "text": mesaj},
                timeout=15,
            )
        for konu in konular:
            if not konu:
                continue
            requests.post(
                f"https://ntfy.sh/{konu}",
                data=mesaj.encode("utf-8"),
                headers={"Priority": "urgent", "Tags": "rotating_light"},
                timeout=15,
            )
    except requests.RequestException as e:
        print(f"[!] Bildirim gönderilemedi: {e}")


def kontenjanlari_cek() -> dict:
    """Tum listeleri tarar; {crn: (ders_adi, kontenjan, yazilan)} döndürür."""
    sonuc = {}
    for url in DERS_URLLERI:
        r = requests.get(url, headers=HEADERS, timeout=20)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")

        for satir in soup.find_all("tr"):
            hucreler = [td.get_text(" ", strip=True) for td in satir.find_all("td")]
            if len(hucreler) < 11:
                continue
            crn = hucreler[0]
            if crn in TAKIP_EDILEN_CRNLER:
                # Sütunlar: 0 CRN, 1 Kod, 2 Ad, ..., 9 Kontenjan, 10 Yazılan
                try:
                    sonuc[crn] = (hucreler[2], int(hucreler[9]), int(hucreler[10]))
                except ValueError:
                    pass
    return sonuc


def otomatik_kayit_dene(crn: str, ders_adi: str) -> None:
    """
    Kontenjan açılan CRN için otomatik ders kaydı dener.
    Sonucu bildirim olarak gönderir.
    """
    if crn in KAYDEDILEN_CRNLER:
        print(f"[OBS] {crn} zaten kaydedildi, tekrar denenmeyecek.")
        return

    config = TAKIP_CONFIG.get(crn, {})
    if not config.get("oto_kayit", False):
        return

    if not OBS_KULLANICI or not OBS_SIFRE:
        bildirim_gonder(
            f"⚠️ OTOMATİK KAYIT YAPILAMADI!\n{crn} {ders_adi}\n"
            "Sebep: OBS_KULLANICI veya OBS_SIFRE tanımlı değil.\n"
            "Elle kayıt yapmanız gerekiyor!",
            crn,
        )
        return

    bildirim_gonder(
        f"⏳ Otomatik kayıt deneniyor...\n{crn} {ders_adi}",
        crn,
    )

    sonuc = kayit_dene(OBS_KULLANICI, OBS_SIFRE, crn)

    if sonuc["basarili"]:
        KAYDEDILEN_CRNLER.add(crn)
        bildirim_gonder(
            f"✅ DERS KAYDEDİLDİ!\n{crn} {ders_adi}\n{sonuc['mesaj']}",
            crn,
        )
    else:
        bildirim_gonder(
            f"❌ OTOMATİK KAYIT BAŞARISIZ!\n{crn} {ders_adi}\n"
            f"Hata: {sonuc['mesaj']}\n"
            "Elle kayıt yapmayı deneyin!",
            crn,
        )


def main() -> None:
    # Başlangıç kontrolleri
    oto_kayit_var = any(cfg.get("oto_kayit") for cfg in TAKIP_CONFIG.values())

    for crn, cfg in TAKIP_CONFIG.items():
        if not cfg.get("ntfy"):
            print(f"[!] {crn} için ntfy konusu tanımlı değil, bildirim gitmeyecek.")

    if oto_kayit_var and (not OBS_KULLANICI or not OBS_SIFRE):
        print("[!] ⚠️ Otomatik kayıt aktif CRN'ler var ama OBS_KULLANICI/OBS_SIFRE tanımlı değil!")
        print("[!] Otomatik kayıt çalışmayacak, sadece bildirim gönderilecek.")

    if oto_kayit_var and OBS_KULLANICI:
        oto_crnler = [c for c, cfg in TAKIP_CONFIG.items() if cfg.get("oto_kayit")]
        print(f"[*] Otomatik kayıt aktif CRN'ler: {', '.join(oto_crnler)}")

    bitis = time.time() + CALISMA_SURESI_DK * 60 if CALISMA_SURESI_DK else None

    if not bitis:
        basla_mesaj = "✅ Kontenjan botu başladı: " + ", ".join(TAKIP_EDILEN_CRNLER)
        if oto_kayit_var and OBS_KULLANICI:
            basla_mesaj += "\n🤖 Otomatik kayıt: AKTİF"
        bildirim_gonder(basla_mesaj)

    onceki = {}
    ardisik_hata = 0

    while bitis is None or time.time() < bitis:
        zaman = datetime.now(TR_SAATI).strftime("%H:%M:%S")
        try:
            veri = kontenjanlari_cek()
            ardisik_hata = 0

            if not veri:
                print(f"[{zaman}] Uyarı: CRN'ler sayfada bulunamadı.")

            for crn, (ad, kont, yaz) in veri.items():
                print(f"[{zaman}] {crn} {ad}: {yaz}/{kont}", flush=True)
                eski = onceki.get(crn)

                if eski:
                    _, eski_kont, eski_yaz = eski
                    if kont > eski_kont:
                        # KONTENJAN ARTTI
                        bildirim_gonder(
                            f"🚨 KONTENJAN ARTTI!\n{crn} {ad}\n"
                            f"{eski_kont} → {kont} (yazılan: {yaz})",
                            crn,
                        )
                        if yaz < kont:
                            otomatik_kayit_dene(crn, ad)

                    elif yaz < kont and not (eski_yaz < eski_kont):
                        # BOŞ YER AÇILDI (biri dersi bıraktı)
                        bildirim_gonder(
                            f"🟢 BOŞ YER AÇILDI!\n{crn} {ad}\nDurum: {yaz}/{kont}", crn
                        )
                        otomatik_kayit_dene(crn, ad)

                elif yaz < kont:
                    bildirim_gonder(f"🟢 Şu an yer var: {crn} {ad} ({yaz}/{kont})", crn)
                    otomatik_kayit_dene(crn, ad)

                onceki[crn] = (ad, kont, yaz)

        except Exception as e:
            ardisik_hata += 1
            print(f"[{zaman}] Hata: {e}", flush=True)
            if ardisik_hata == 5:
                bildirim_gonder(f"⚠️ Bot 5 kez üst üste hata aldı: {e}")

        time.sleep(KONTROL_ARALIGI_SN + random.randint(0, 15))


if __name__ == "__main__":
    main()
