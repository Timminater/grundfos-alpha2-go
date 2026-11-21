# Grundfos ALPHA2 GO voor Home Assistant

[![Open in HACS](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=Timminater&repository=grundfos-alpha2-go&category=integration)

Ontwikkelversie voor lokaal uitlezen en instellen van een Grundfos ALPHA2 GO, inclusief de beoogde ALPHA2 GO K 25-75. Home Assistant gebruikt een bestaande ESPHome Bluetooth-proxy met actieve verbindingen en ondersteuning voor koppelen.

De basis is [83noit/ha-grundfos-ble v0.2.1](https://github.com/83noit/ha-grundfos-ble/tree/v0.2.1), met de bijbehorende MIT-licentie. Sensoren, ontdekking en koppeling komen uit die versie. De protocolopnames van [Birkemosen](https://github.com/Birkemosen/esphome-alpha2go/blob/main/docs/ble-traffic.md) dienen als technische referentie.

## Installatie voor ontwikkeling

Vereist Home Assistant 2026.11 of nieuwer. Voeg via de HACS-knop hierboven
`Timminater/grundfos-alpha2-go` toe als aangepaste repository in de categorie
**Integration**, installeer **Grundfos Bluetooth** en herstart Home Assistant.
Bij handmatige installatie kopieer je `custom_components/grundfos_ble` naar
de gelijknamige map onder de Home Assistant-configuratiemap.
Deze ontwikkelversie gebruikt hetzelfde integratiedomein als 83noit. Gebruik één versie tegelijk en bewaar vóór een vervanging de geïnstalleerde bestanden.

Voeg **Grundfos Bluetooth** toe onder **Instellingen → Apparaten en diensten**. Druk op de Bluetooth-knop op de pomp zodra de koppelwizard daarom vraagt. Ontkoppel de Grundfos GO-app voor het koppelen of uitlezen.

## Pompbediening

Alleen uitlezen is de standaard. Schakel **Experimentele pompbediening** in via de opties van de pomp. De bediening wordt beschikbaar zodra Home Assistant de bijbehorende gegevens heeft uitgelezen.

| Bediening | Entiteit of actie |
| --- | --- |
| Constante en proportionele druk, constante curve, constant debiet, beide AUTOADAPT-modi en de pompstanden I–III | Keuzelijst **Regelmodus en pompstand** |
| Opvoerhoogte, toerental of debiet van de actieve regelmodus | Numeriek setpoint met grenzen uit de pomp |
| Minimum- en maximumdebiet | Eén getal en één schakelaar per ondersteunde begrenzer |
| Nachtverlaging | Schakelaar **AutoNight** |
| Start, stop, minimumcurve en maximumcurve | Vier knoppen |
| Werkpunt bij een bepaald debiet en opvoerhoogte | Actie `grundfos_ble.set_duty_point`. Selecteer de pomp en vul debiet (m³/h) en opvoerhoogte (m) in. Vereist een actieve constante drukmodus of een ondersteunde lineaire proportionele drukcurve. |

Een preset kan zowel de modus als het setpoint veranderen. Bij een gedeeltelijk geslaagde wijziging controleer je de gerapporteerde modus en het setpoint op de pomp. Bij een verloren Bluetooth-bevestiging leest de integratie de instelling zo mogelijk opnieuw uit. Ze verstuurt dezelfde schrijfopdracht nooit automatisch nogmaals.

Deze versie is een prototype. Verifieer eerst stabiele sensorupdates via een bestaande proxy. Controleer daarna één kleine, omkeerbare setpointwijziging op het pompdisplay en in Home Assistant, en herstel vervolgens het eerdere setpoint.
