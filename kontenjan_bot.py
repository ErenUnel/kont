"""
İTÜ OBS kontenjan takip botu (GitHub Actions uyumlu)
- Belirlenen CRN'lerin kontenjanını ve yazılan sayısını dakikada bir kontrol eder
- Kontenjan artarsa veya boş yer açılırsa bildirim yollar (ntfy veya Telegram)

Gizli bilgiler ortam değişkeninden okunur:
  NTFY_KONU, NTFY_KONU_SNT           -> ntfy konu adlari
  TELEGRAM_TOKEN, TELEGRAM_CHAT_ID   -> Telegram için
"""

import os
import random
import time
from datetime import datetime, timedelta, timezone

import requests
from bs4 import BeautifulSoup

# ================== AYARLAR ==================
TEMEL_URL = "https://obs.itu.edu.tr/public/DersProgram/DersProgramSearch?programSeviyeTipiAnahtari=LS&dersBransKoduId="

# Taranacak ders listeleri (URL'nin sonundaki numara ders kodunu belirler)
DERS_URLLERI = [
    TEMEL_URL + "3",              # BLG
    TEMEL_URL + "193",            # SNT
    TEMEL_URL + "226",            # FRA
    TEMEL_URL + "43",             # ATA
]

KANALLAR = {
    "12575": os.getenv("NTFY_KONU", ""),        # Computer Vision  -> senin konun
    "10729": os.getenv("NTFY_KONU_SNT", ""),    # Sinema Sanatı
    "10683": os.getenv("NTFY_KONU_SNT", ""),    # French I
    "10755": os.getenv("NTFY_KONU_ATA", ""),    # SNT 211E
    "14638": os.getenv("NTFY_KONU_ATA", ""),    # SNT 211E
    "10134": os.getenv("NTFY_KONU_ATA", ""),    # ATA -> yeni kanal
}

TAKIP_EDILEN_CRNLER = list(KANALLAR)

KONTROL_ARALIGI_SN = 60  # 60 sn'nin altına inme

# Kaç dakika sonra kapansın (GitHub Actions için). 0 = sonsuza kadar çalış
CALISMA_SURESI_DK = int(os.getenv("CALISMA_SURESI_DK", "0"))

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
# =============================================

HEADERS = {
    "User-Agent": "Mozilla/5.0 (kontenjan-takip, kisisel kullanim)",
    "X-Requested-With": "XMLHttpRequest",
    "Referer": "https://obs.itu.edu.tr/public/DersProgram",
}
TR_SAATI = timezone(timedelta(hours=3))


def bildirim_gonder(mesaj: str, crn: str = "") -> None:
    """crn verilirse sadece o dersin konusuna, verilmezse tum konulara gonderir."""
    print(f"[BİLDİRİM] {mesaj}")

    if crn:
        konular = [KANALLAR.get(crn, "")]
    else:
        konular = list(dict.fromkeys(KANALLAR.values()))

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


def main() -> None:
    for crn, konu in KANALLAR.items():
        if not konu:
            print(f"[!] {crn} için ntfy konusu tanımlı değil, bildirim gitmeyecek.")

    bitis = time.time() + CALISMA_SURESI_DK * 60 if CALISMA_SURESI_DK else None
    if not bitis:
        bildirim_gonder("✅ Kontenjan botu başladı: " + ", ".join(TAKIP_EDILEN_CRNLER))

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
                        bildirim_gonder(
                            f"🚨 KONTENJAN ARTTI!\n{crn} {ad}\n"
                            f"{eski_kont} → {kont} (yazılan: {yaz})",
                            crn,
                        )
                    elif yaz < kont and not (eski_yaz < eski_kont):
                        bildirim_gonder(
                            f"🟢 BOŞ YER AÇILDI!\n{crn} {ad}\nDurum: {yaz}/{kont}", crn
                        )
                elif yaz < kont:
                    bildirim_gonder(f"🟢 Şu an yer var: {crn} {ad} ({yaz}/{kont})", crn)

                onceki[crn] = (ad, kont, yaz)

        except Exception as e:
            ardisik_hata += 1
            print(f"[{zaman}] Hata: {e}", flush=True)
            if ardisik_hata == 5:
                bildirim_gonder(f"⚠️ Bot 5 kez üst üste hata aldı: {e}")

        time.sleep(KONTROL_ARALIGI_SN + random.randint(0, 15))


if __name__ == "__main__":
    main()
