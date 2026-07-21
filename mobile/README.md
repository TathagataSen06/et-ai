# Netra Mobile Scanner (React Native Expo)

Camera-based currency scanner that posts captures (with GPS) to the Netra API.

```bash
cd mobile
npm install
npx expo start          # scan the QR with Expo Go on a phone
```

Set the API URL field in the app header to your backend's LAN address
(e.g. `http://192.168.1.10:8000`) — `localhost` on a phone points at the phone.

Not built in CI (needs the Expo toolchain + a device/emulator); the web scanner
page covers the same API path for automated verification.
