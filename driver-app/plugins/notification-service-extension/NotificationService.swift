//
//  NotificationService.swift
//  OfferCardService — iOS Notification Service Extension
//
//  Downloads the ride-offer fare banner and attaches it so the offer push
//  shows the rich image (the iOS equivalent of the Android BigPicture). The
//  backend sends the URL two ways; we read whichever is present:
//    - data key  "offer_card_url"      (our dispatch payload)
//    - fcm_options.image               (set by APNSFCMOptions)
//
//  Requires the push to carry `mutable-content: 1` (the backend sets it for
//  dispatch). Self-contained — no Firebase dependency in the extension. Any
//  failure falls through to the original (text) notification, so an offer is
//  never lost to a missing/slow image.
//
//  NOTE: this file is installed into the iOS project by
//  plugins/withOfferCardNotificationService.js at prebuild — it is not part of
//  the JS bundle.
//

import UserNotifications

final class NotificationService: UNNotificationServiceExtension {
    private var contentHandler: ((UNNotificationContent) -> Void)?
    private var bestAttempt: UNMutableNotificationContent?

    override func didReceive(
        _ request: UNNotificationRequest,
        withContentHandler contentHandler: @escaping (UNNotificationContent) -> Void
    ) {
        self.contentHandler = contentHandler
        bestAttempt = request.content.mutableCopy() as? UNMutableNotificationContent

        guard let content = bestAttempt else {
            contentHandler(request.content)
            return
        }

        let userInfo = request.content.userInfo
        var imageURLString = userInfo["offer_card_url"] as? String
        if imageURLString == nil,
           let fcmOptions = userInfo["fcm_options"] as? [String: Any] {
            imageURLString = fcmOptions["image"] as? String
        }

        guard let urlString = imageURLString,
              !urlString.isEmpty,
              let url = URL(string: urlString) else {
            contentHandler(content)
            return
        }

        let task = URLSession.shared.downloadTask(with: url) { [weak self] tempURL, _, _ in
            guard let self = self else { return }
            defer { self.deliver() }

            guard let tempURL = tempURL else { return }
            let fm = FileManager.default
            // Give it a .png suffix so UNNotificationAttachment infers the type.
            let dest = fm.temporaryDirectory.appendingPathComponent(UUID().uuidString + ".png")
            do {
                try? fm.removeItem(at: dest)
                try fm.moveItem(at: tempURL, to: dest)
                let attachment = try UNNotificationAttachment(
                    identifier: "offer-card",
                    url: dest,
                    options: nil
                )
                content.attachments = [attachment]
            } catch {
                // Leave the text notification as-is.
            }
        }
        task.resume()
    }

    override func serviceExtensionTimeWillExpire() {
        // System is about to kill us — deliver whatever we have.
        deliver()
    }

    private func deliver() {
        guard let handler = contentHandler, let content = bestAttempt else { return }
        contentHandler = nil
        handler(content)
    }
}
