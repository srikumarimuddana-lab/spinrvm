# Spinr — Full User-Facing Message List (manual validation)

Companion to `2026-09-18-user-facing-message-audit.md`. Every message a human can see,
grouped by surface then by screen/route. Tick each row as you trigger it in the app.

`OK` = reads as plain English. `FLAG` = contains an internal identifier, see the `Issue` column.
`REVIEW` = backend 5xx, text is suppressed and the user reads "Internal server error".

---

## RIDER APP  (429 messages)

### `BookingProposalCard`  (4)

| ✓ | Trigger | Message the user sees | Verdict | Issue | Source |
|---|---|---|---|---|---|
| ☐ | setError | Blocked by company policy: ${check.reasons.join(' ')} | OK |  | `rider-app/components/BookingProposalCard.tsx:191` |
| ☐ | setError | Open booking | OK |  | `rider-app/components/BookingProposalCard.tsx:212` |
| ☐ | setError | Review in booking | OK |  | `rider-app/components/BookingProposalCard.tsx:191` |
| ☐ | setError | This card needs extra authentication — finish booking on the payment screen. | OK |  | `rider-app/components/BookingProposalCard.tsx:212` |

### `account`  (2)

| ✓ | Trigger | Message the user sees | Verdict | Issue | Source |
|---|---|---|---|---|---|
| ☐ | Alert.alert | Are you sure you want to sign out? | OK |  | `rider-app/app/(tabs)/account.tsx:332` |
| ☐ | Alert.alert | Sign Out | OK |  | `rider-app/app/(tabs)/account.tsx:332` |

### `ai-assistant`  (6)

| ✓ | Trigger | Message the user sees | Verdict | Issue | Source |
|---|---|---|---|---|---|
| ☐ | showToast | Allow microphone access in Settings to use voice input. | OK |  | `rider-app/app/ai-assistant.tsx:265` |
| ☐ | showToast | Could not capture audio — please try again or type instead. | OK |  | `rider-app/app/ai-assistant.tsx:249` |
| ☐ | showToast | Could not start voice capture — please type instead. | OK |  | `rider-app/app/ai-assistant.tsx:272` |
| ☐ | showToast | Microphone needed | OK |  | `rider-app/app/ai-assistant.tsx:265` |
| ☐ | showToast | Voice input failed | OK |  | `rider-app/app/ai-assistant.tsx:249` |
| ☐ | showToast | Voice input failed | OK |  | `rider-app/app/ai-assistant.tsx:272` |

### `become-driver`  (11)

| ✓ | Trigger | Message the user sees | Verdict | Issue | Source |
|---|---|---|---|---|---|
| ☐ | showToast | Application Submitted! | OK |  | `rider-app/app/become-driver.tsx:265` |
| ☐ | showToast | Could not submit your application. Please try again. | OK |  | `rider-app/app/become-driver.tsx:268` |
| ☐ | showToast | Could not upload document. Please try again. | OK |  | `rider-app/app/become-driver.tsx:168` |
| ☐ | showToast | Document uploaded successfully | OK |  | `rider-app/app/become-driver.tsx:166` |
| ☐ | showToast | Invalid Year | OK |  | `rider-app/app/become-driver.tsx:180` |
| ☐ | showToast | Missing Documents | OK |  | `rider-app/app/become-driver.tsx:187` |
| ☐ | showToast | Please provide: ${missing.join(', ')} | OK |  | `rider-app/app/become-driver.tsx:187` |
| ☐ | showToast | Registration Failed | OK |  | `rider-app/app/become-driver.tsx:268` |
| ☐ | showToast | Upload Failed | OK |  | `rider-app/app/become-driver.tsx:168` |
| ☐ | showToast | Vehicle must be 9 years old or newer. | OK |  | `rider-app/app/become-driver.tsx:180` |
| ☐ | showToast | Waiting for approval. To start driving, download the Spinr Driver app. | OK |  | `rider-app/app/become-driver.tsx:265` |

### `booking`  (24)

| ✓ | Trigger | Message the user sees | Verdict | Issue | Source |
|---|---|---|---|---|---|
| ☐ | HTTP 403 | (rider_row or {}).get('status_reason') or 'Your account has been deactivated due to policy violations. Please contact support.' | FLAG | code identifier: rider_row, status_reason | `backend/routes/rides/booking.py:426` |
| ☐ | HTTP 403 | (rider_row or {}).get('status_reason') or 'Your account is currently suspended. Please contact support.' | FLAG | code identifier: rider_row, status_reason | `backend/routes/rides/booking.py:444` |
| ☐ | HTTP 409 | Duplicate ride request | OK |  | `backend/routes/rides/booking.py:1605` |
| ☐ | HTTP 400 | Insufficient wallet balance. Estimated fare is ${grand_total}, wallet has ${_f(wallet_balance)}. Please top up your wallet or switch to card payment. | OK |  | `backend/routes/rides/booking.py:925` |
| ☐ | HTTP 400 | Invalid vehicle type for this service area | OK |  | `backend/routes/rides/booking.py:757` |
| ☐ | HTTP 400 | No payment method on file. Please add a card first. | OK |  | `backend/routes/rides/booking.py:472` |
| ☐ | HTTP 400 | Select a payment card before booking. | OK |  | `backend/routes/rides/booking.py:487` |
| ☐ | HTTP 409 | You already have an active ride | OK |  | `backend/routes/rides/booking.py:1595` |
| ☐ | HTTP 402 | {'code': 'CARD_DECLINED', 'message': "Card authentication wasn't completed. Please try booking again."} | OK |  | `backend/routes/rides/booking.py:232` |
| ☐ | HTTP 402 | {'code': 'CARD_DECLINED', 'message': "This authorization can't be reused. Please try booking again."} | OK |  | `backend/routes/rides/booking.py:209` |
| ☐ | HTTP 402 | {'code': 'CARD_DECLINED', 'message': 'No payment method on file. Please add a card first.'} | OK |  | `backend/routes/rides/booking.py:189` |
| ☐ | HTTP 402 | {'code': 'CARD_DECLINED', 'message': 'Your card was declined. Please update your payment method and try booking again.', 'decline_code': decline_code} | OK |  | `backend/routes/rides/booking.py:156` |
| ☐ | HTTP 400 | {'code': 'DROPOFF_ADDRESS_MISMATCH', 'message': f"Dropoff address and location don't match: {_dropoff_mismatch_reason}"} | OK |  | `backend/routes/rides/booking.py:729` |
| ☐ | HTTP 400 | {'code': 'OUTSIDE_SERVICE_AREA', 'message': 'Sorry, one of your stops is outside our coverage area. Please choose stops within a serviced zone.'} | OK |  | `backend/routes/rides/booking.py:689` |
| ☐ | HTTP 400 | {'code': 'OUTSIDE_SERVICE_AREA', 'message': 'Sorry, your dropoff location is outside our coverage area. Please choose a dropoff within a serviced zone.'} | OK |  | `backend/routes/rides/booking.py:665` |
| ☐ | HTTP 400 | {'code': 'OUTSIDE_SERVICE_AREA', 'message': 'Sorry, your pickup location is outside our coverage area. Please choose a pickup within a serviced zone.'} | OK |  | `backend/routes/rides/booking.py:641` |
| ☐ | HTTP 400 | {'code': 'PICKUP_ADDRESS_MISMATCH', 'message': f"Pickup address and location don't match: {_pickup_mismatch_reason}"} | OK |  | `backend/routes/rides/booking.py:721` |
| ☐ | HTTP 403 | {'message': "You're no longer an active member of this company account.", 'failed_rules': ['membership_inactive']} | OK |  | `backend/routes/rides/booking.py:1049` |
| ☐ | HTTP 403 | {'message': message, 'failed_rules': _policy_result.failed_rules} | OK |  | `backend/routes/rides/booking.py:993` |
| ☐ | HTTP 400 | {'reason': 'allowance_low'} | OK |  | `backend/routes/rides/booking.py:1021` |
| ☐ | HTTP 400 | {'reason': 'allowance_low'} | OK |  | `backend/routes/rides/booking.py:1220` |
| ☐ | HTTP 400 | {'reason': 'company_inactive'} | OK |  | `backend/routes/rides/booking.py:1160` |
| ☐ | HTTP 400 | {'reason': 'no_corporate_membership'} | OK |  | `backend/routes/rides/booking.py:1169` |
| ☐ | HTTP 400 | {'reason': 'policy_violation', 'failed_rules': _policy_result.failed_rules} | OK |  | `backend/routes/rides/booking.py:1187` |

### `cancellation`  (1)

| ✓ | Trigger | Message the user sees | Verdict | Issue | Source |
|---|---|---|---|---|---|
| ☐ | HTTP 409 | Ride can no longer be cancelled (it has started or already ended) | OK |  | `backend/routes/rides/cancellation.py:99` |

### `chat`  (10)

| ✓ | Trigger | Message the user sees | Verdict | Issue | Source |
|---|---|---|---|---|---|
| ☐ | HTTP 400 | Cannot send messages on a cancelled ride | OK |  | `backend/routes/rides/chat.py:128` |
| ☐ | HTTP 403 | Not a participant | OK |  | `backend/routes/rides/chat.py:231` |
| ☐ | HTTP 403 | Not authorized to send messages in this ride | OK |  | `backend/routes/rides/chat.py:141` |
| ☐ | HTTP 403 | Not authorized to track this ride | OK |  | `backend/routes/rides/chat.py:82` |
| ☐ | HTTP 400 | Post-trip chat window has expired (24 hours) | OK |  | `backend/routes/rides/chat.py:134` |
| ☐ | HTTP 404 | Ride not found | OK |  | `backend/routes/rides/chat.py:32` |
| ☐ | HTTP 404 | Ride not found | OK |  | `backend/routes/rides/chat.py:74` |
| ☐ | HTTP 404 | Ride not found | OK |  | `backend/routes/rides/chat.py:124` |
| ☐ | HTTP 404 | Ride not found | OK |  | `backend/routes/rides/chat.py:218` |
| ☐ | HTTP 404 | Ride not found | OK |  | `backend/routes/rides/chat.py:43` |

### `chat-driver`  (2)

| ✓ | Trigger | Message the user sees | Verdict | Issue | Source |
|---|---|---|---|---|---|
| ☐ | showToast | Could not send message. Check your connection. | OK |  | `rider-app/app/chat-driver.tsx:157` |
| ☐ | showToast | Send Failed | OK |  | `rider-app/app/chat-driver.tsx:157` |

### `driver-arrived`  (4)

| ✓ | Trigger | Message the user sees | Verdict | Issue | Source |
|---|---|---|---|---|---|
| ☐ | showToast | Copied! | OK |  | `rider-app/app/driver-arrived.tsx:222` |
| ☐ | showToast | Could not cancel | OK |  | `rider-app/app/driver-arrived.tsx:102` |
| ☐ | showToast | Could not cancel your ride. Please try again. | OK |  | `rider-app/app/driver-arrived.tsx:102` |
| ☐ | showToast | OTP copied to clipboard. | OK |  | `rider-app/app/driver-arrived.tsx:222` |

### `driver-arriving`  (8)

| ✓ | Trigger | Message the user sees | Verdict | Issue | Source |
|---|---|---|---|---|---|
| ☐ | showToast | A $${cancellationFee.toFixed(2)} fee now applies if you cancel. | OK |  | `rider-app/app/driver-arriving.tsx:151` |
| ☐ | showToast | Cancel Failed | OK |  | `rider-app/app/driver-arriving.tsx:429` |
| ☐ | showToast | Copied! | OK |  | `rider-app/app/driver-arriving.tsx:521` |
| ☐ | showToast | Could not cancel the ride. Please try again. | OK |  | `rider-app/app/driver-arriving.tsx:429` |
| ☐ | showToast | Driver details copied to clipboard. | OK |  | `rider-app/app/driver-arriving.tsx:521` |
| ☐ | showToast | Free cancel window closed | OK |  | `rider-app/app/driver-arriving.tsx:151` |
| ☐ | showToast | Live trip tracking is not set up yet. Please contact support. | OK |  | `rider-app/app/driver-arriving.tsx:506` |
| ☐ | showToast | Tracking Not Configured | OK |  | `rider-app/app/driver-arriving.tsx:506` |

### `emergency-contacts`  (6)

| ✓ | Trigger | Message the user sees | Verdict | Issue | Source |
|---|---|---|---|---|---|
| ☐ | showToast | ${trimmedName} has been added as an emergency contact. | OK |  | `rider-app/app/emergency-contacts.tsx:118` |
| ☐ | showToast | Contact Added | OK |  | `rider-app/app/emergency-contacts.tsx:118` |
| ☐ | showToast | Could Not Add | OK |  | `rider-app/app/emergency-contacts.tsx:120` |
| ☐ | showToast | Could not add contact. | OK |  | `rider-app/app/emergency-contacts.tsx:120` |
| ☐ | showToast | Could not remove contact. | OK |  | `rider-app/app/emergency-contacts.tsx:141` |
| ☐ | showToast | Remove Failed | OK |  | `rider-app/app/emergency-contacts.tsx:141` |

### `estimates`  (3)

| ✓ | Trigger | Message the user sees | Verdict | Issue | Source |
|---|---|---|---|---|---|
| ☐ | HTTP 400 | {'code': 'OUTSIDE_SERVICE_AREA', 'message': 'Sorry, one of your stops is outside our coverage area. Please choose stops within a serviced zone.'} | OK |  | `backend/routes/rides/estimates.py:252` |
| ☐ | HTTP 400 | {'code': 'OUTSIDE_SERVICE_AREA', 'message': 'Sorry, your dropoff location is outside our coverage area. Please choose a dropoff within a serviced zone.'} | OK |  | `backend/routes/rides/estimates.py:231` |
| ☐ | HTTP 400 | {'code': 'OUTSIDE_SERVICE_AREA', 'message': 'Sorry, your pickup location is outside our coverage area. Please choose a pickup within a serviced zone.'} | OK |  | `backend/routes/rides/estimates.py:211` |

### `index`  (8)

| ✓ | Trigger | Message the user sees | Verdict | Issue | Source |
|---|---|---|---|---|---|
| ☐ | showToast | AI Ride Booking is coming soon! | OK |  | `rider-app/app/(tabs)/index.tsx:347` |
| ☐ | showToast | Coming Soon | OK |  | `rider-app/app/(tabs)/index.tsx:347` |
| ☐ | showToast | Enable location in Settings to use Spinr. | OK |  | `rider-app/app/(tabs)/index.tsx:159` |
| ☐ | showToast | Enable location in Settings to use Spinr. | OK |  | `rider-app/app/(tabs)/index.tsx:363` |
| ☐ | showToast | Location Required | OK |  | `rider-app/app/(tabs)/index.tsx:159` |
| ☐ | showToast | Location Required | OK |  | `rider-app/app/(tabs)/index.tsx:363` |
| ☐ | showToast | Notification permissions are disabled in device settings. | OK |  | `rider-app/app/(tabs)/index.tsx:103` |
| ☐ | showToast | Permission Required | OK |  | `rider-app/app/(tabs)/index.tsx:103` |

### `lifecycle`  (13)

| ✓ | Trigger | Message the user sees | Verdict | Issue | Source |
|---|---|---|---|---|---|
| ☐ | HTTP 403 | ERR_DRIVER_ONLY | FLAG | constant: ERR_DRIVER_ONLY | `backend/routes/rides/lifecycle.py:112` |
| ☐ | HTTP 403 | Not authorized | OK |  | `backend/routes/rides/lifecycle.py:61` |
| ☐ | HTTP 403 | Not authorized | OK |  | `backend/routes/rides/lifecycle.py:119` |
| ☐ | HTTP 403 | Not authorized | OK |  | `backend/routes/rides/lifecycle.py:199` |
| ☐ | HTTP 403 | Not available in production | OK |  | `backend/routes/rides/lifecycle.py:56` |
| ☐ | HTTP 400 | Ride is not in progress | OK |  | `backend/routes/rides/lifecycle.py:201` |
| ☐ | HTTP 409 | Ride is not in progress | OK |  | `backend/routes/rides/lifecycle.py:216` |
| ☐ | HTTP 404 | Ride not found | OK |  | `backend/routes/rides/lifecycle.py:59` |
| ☐ | HTTP 404 | Ride not found | OK |  | `backend/routes/rides/lifecycle.py:115` |
| ☐ | HTTP 404 | Ride not found | OK |  | `backend/routes/rides/lifecycle.py:197` |
| ☐ | HTTP 400 | This trip can't be started yet. Make sure you've arrived at the pickup first. | OK |  | `backend/routes/rides/lifecycle.py:121` |
| ☐ | HTTP 410 | Use POST /drivers/rides/{ride_id}/verify-otp to start a ride in production. | OK |  | `backend/routes/rides/lifecycle.py:106` |
| ☐ | HTTP 409 | f"We couldn't start this trip — it's {_guard_phrase}. Refresh to see its latest status." if _guard_phrase else "We couldn't start this trip. Refresh to see its latest status." | OK |  | `backend/routes/rides/lifecycle.py:139` |

### `login`  (5)

| ✓ | Trigger | Message the user sees | Verdict | Issue | Source |
|---|---|---|---|---|---|
| ☐ | showToast | Code Not Sent | OK |  | `rider-app/app/login.tsx:92` |
| ☐ | showToast | Connection Error | OK |  | `rider-app/app/login.tsx:104` |
| ☐ | showToast | Could not send verification code. Please try again. | OK |  | `rider-app/app/login.tsx:92` |
| ☐ | showToast | Sign-in Unavailable | OK |  | `rider-app/app/login.tsx:102` |
| ☐ | showToast | Unable to reach server. Please check your connection. | OK |  | `rider-app/app/login.tsx:104` |

### `lost-and-found-chat`  (12)

| ✓ | Trigger | Message the user sees | Verdict | Issue | Source |
|---|---|---|---|---|---|
| ☐ | Alert.alert | Add a Photo | OK |  | `rider-app/app/lost-and-found-chat.tsx:164` |
| ☐ | showToast | Camera access is needed to take a photo. | OK |  | `rider-app/app/lost-and-found-chat.tsx:176` |
| ☐ | showToast | Could not load case. Pull to refresh. | OK |  | `rider-app/app/lost-and-found-chat.tsx:89` |
| ☐ | showToast | Could not send message. | OK |  | `rider-app/app/lost-and-found-chat.tsx:153` |
| ☐ | showToast | Could not send the photo. Please try again. | OK |  | `rider-app/app/lost-and-found-chat.tsx:221` |
| ☐ | showToast | Couldn't Load Case | OK |  | `rider-app/app/lost-and-found-chat.tsx:89` |
| ☐ | showToast | Permission Needed | OK |  | `rider-app/app/lost-and-found-chat.tsx:176` |
| ☐ | Alert.alert | Photo Library | OK |  | `rider-app/app/lost-and-found-chat.tsx:164` |
| ☐ | showToast | Photo access is needed to attach an image. | OK |  | `rider-app/app/lost-and-found-chat.tsx:176` |
| ☐ | showToast | Send Failed | OK |  | `rider-app/app/lost-and-found-chat.tsx:153` |
| ☐ | Alert.alert | Show the driver what you are looking for | OK |  | `rider-app/app/lost-and-found-chat.tsx:164` |
| ☐ | showToast | Upload Failed | OK |  | `rider-app/app/lost-and-found-chat.tsx:221` |

### `lost_found`  (4)

| ✓ | Trigger | Message the user sees | Verdict | Issue | Source |
|---|---|---|---|---|---|
| ☐ | HTTP 400 | Lost items can only be reported for completed rides | OK |  | `backend/routes/rides/lost_found.py:47` |
| ☐ | HTTP 400 | No driver assigned to this ride | OK |  | `backend/routes/rides/lost_found.py:54` |
| ☐ | HTTP 403 | Not authorized | OK |  | `backend/routes/rides/lost_found.py:45` |
| ☐ | HTTP 404 | Ride not found | OK |  | `backend/routes/rides/lost_found.py:43` |

### `manage-cards`  (12)

| ✓ | Trigger | Message the user sees | Verdict | Issue | Source |
|---|---|---|---|---|---|
| ☐ | showToast | Card Added | OK |  | `rider-app/app/manage-cards.tsx:190` |
| ☐ | showToast | Card Added | OK |  | `rider-app/app/manage-cards.tsx:195` |
| ☐ | showToast | Card Not Added | OK |  | `rider-app/app/manage-cards.tsx:197` |
| ☐ | showToast | Card added successfully | OK |  | `rider-app/app/manage-cards.tsx:195` |
| ☐ | showToast | Charging your ride… | OK |  | `rider-app/app/manage-cards.tsx:190` |
| ☐ | showToast | Could not add card. Please try again. | OK |  | `rider-app/app/manage-cards.tsx:197` |
| ☐ | showToast | Could not process card. Please try again. | OK |  | `rider-app/app/manage-cards.tsx:181` |
| ☐ | showToast | Could not remove card. Please try again. | OK |  | `rider-app/app/manage-cards.tsx:258` |
| ☐ | showToast | Could not set default card. Please try again. | OK |  | `rider-app/app/manage-cards.tsx:223` |
| ☐ | showToast | Processing Failed | OK |  | `rider-app/app/manage-cards.tsx:181` |
| ☐ | showToast | Remove Failed | OK |  | `rider-app/app/manage-cards.tsx:258` |
| ☐ | showToast | Update Failed | OK |  | `rider-app/app/manage-cards.tsx:223` |

### `otp`  (13)

| ✓ | Trigger | Message the user sees | Verdict | Issue | Source |
|---|---|---|---|---|---|
| ☐ | showToast | A new verification code has been sent to your phone. | OK |  | `rider-app/app/otp.tsx:252` |
| ☐ | showToast | A new verification code has been sent to your phone. | OK |  | `rider-app/app/otp.tsx:267` |
| ☐ | showToast | Code Sent | OK |  | `rider-app/app/otp.tsx:252` |
| ☐ | showToast | Code Sent | OK |  | `rider-app/app/otp.tsx:267` |
| ☐ | showToast | Could not resend code. Please try again. | OK |  | `rider-app/app/otp.tsx:276` |
| ☐ | showToast | Could not send a new code. Please try again. | OK |  | `rider-app/app/otp.tsx:254` |
| ☐ | showToast | Invalid Code | OK |  | `rider-app/app/otp.tsx:149` |
| ☐ | showToast | One More Step | OK |  | `rider-app/app/otp.tsx:228` |
| ☐ | showToast | Please agree to our Terms of Service and Privacy Policy to finish creating your account. | OK |  | `rider-app/app/otp.tsx:228` |
| ☐ | showToast | Please enter the ${CODE_LENGTH}-digit code sent to your phone. | OK |  | `rider-app/app/otp.tsx:149` |
| ☐ | showToast | Please wait ${retrySeconds} seconds before requesting another code. | OK |  | `rider-app/app/otp.tsx:274` |
| ☐ | showToast | Too Many Attempts | OK |  | `rider-app/app/otp.tsx:274` |
| ☐ | showToast | Verification Failed | OK |  | `rider-app/app/otp.tsx:237` |

### `payment-confirm`  (9)

| ✓ | Trigger | Message the user sees | Verdict | Issue | Source |
|---|---|---|---|---|---|
| ☐ | showToast | Authentication needed | OK |  | `rider-app/app/payment-confirm.tsx:196` |
| ☐ | showToast | Authentication needed | OK |  | `rider-app/app/payment-confirm.tsx:201` |
| ☐ | showToast | Booking Failed | OK |  | `rider-app/app/payment-confirm.tsx:280` |
| ☐ | showToast | Booking failed | OK |  | `rider-app/app/payment-confirm.tsx:209` |
| ☐ | showToast | Card authentication was not completed. | OK |  | `rider-app/app/payment-confirm.tsx:196` |
| ☐ | showToast | Card authentication was not completed. Please try again. | OK |  | `rider-app/app/payment-confirm.tsx:201` |
| ☐ | showToast | Card authorization could not be completed. Please try again. | OK |  | `rider-app/app/payment-confirm.tsx:209` |
| ☐ | showToast | Could not complete your booking. Please try again. | OK |  | `rider-app/app/payment-confirm.tsx:280` |
| ☐ | showToast | Promo not applied | OK |  | `rider-app/app/payment-confirm.tsx:215` |

### `payments`  (33)

| ✓ | Trigger | Message the user sees | Verdict | Issue | Source |
|---|---|---|---|---|---|
| ☐ | HTTP 400 | Can only tip completed rides | OK |  | `backend/routes/rides/payments.py:164` |
| ☐ | HTTP 404 | Card not found | OK |  | `backend/routes/payments.py:1260` |
| ☐ | HTTP 404 | Card not found | OK |  | `backend/routes/payments.py:1188` |
| ☐ | HTTP 404 | Card not found | OK |  | `backend/routes/payments.py:1168` |
| ☐ | HTTP 400 | ERR_TIP_DUPLICATE | FLAG | constant: ERR_TIP_DUPLICATE | `backend/routes/rides/payments.py:181` |
| ☐ | HTTP 400 | For your security, card details have to be entered in the secure card form. Please add your card again from the payment screen. | OK |  | `backend/routes/payments.py:1021` |
| ☐ | HTTP 400 | Imported historical rides cannot be tipped | OK |  | `backend/routes/rides/payments.py:176` |
| ☐ | HTTP 400 | Mock payments are not supported in production | OK |  | `backend/routes/payments.py:611` |
| ☐ | HTTP 403 | Not authorized | OK |  | `backend/routes/rides/payments.py:405` |
| ☐ | HTTP 403 | Not authorized to confirm this payment | OK |  | `backend/routes/payments.py:692` |
| ☐ | HTTP 403 | Not authorized to pay for this ride | OK |  | `backend/routes/payments.py:76` |
| ☐ | HTTP 403 | Not authorized to tip this ride | OK |  | `backend/routes/rides/payments.py:161` |
| ☐ | HTTP 403 | Payment does not match this ride | OK |  | `backend/routes/payments.py:719` |
| ☐ | HTTP 409 | Payment is processing; please retry in a moment. | OK |  | `backend/routes/rides/payments.py:605` |
| ☐ | HTTP 409 | Payment retry already in progress. Please try again in a moment. | OK |  | `backend/routes/rides/payments.py:575` |
| ☐ | HTTP 404 | Ride not found | OK |  | `backend/routes/payments.py:74` |
| ☐ | HTTP 404 | Ride not found | OK |  | `backend/routes/payments.py:625` |
| ☐ | HTTP 404 | Ride not found | OK |  | `backend/routes/rides/payments.py:158` |
| ☐ | HTTP 404 | Ride not found | OK |  | `backend/routes/rides/payments.py:403` |
| ☐ | HTTP 409 | This payment is already being processed. Give it a moment before trying again. | OK |  | `backend/routes/payments.py:664` |
| ☐ | HTTP 403 | This ride belongs to a different account, so you can't pay for it. | OK |  | `backend/routes/payments.py:627` |
| ☐ | HTTP 400 | Tip amount cannot be negative | OK |  | `backend/routes/rides/payments.py:526` |
| ☐ | HTTP 400 | Tip amount exceeds maximum ($500) | OK |  | `backend/routes/rides/payments.py:528` |
| ☐ | HTTP 400 | Tip amount must be greater than zero | OK |  | `backend/routes/rides/payments.py:154` |
| ☐ | HTTP 404 | User not found | OK |  | `backend/routes/payments.py:285` |
| ☐ | HTTP 404 | User not found during Stripe customer creation | OK |  | `backend/routes/payments.py:326` |
| ☐ | HTTP 400 | We couldn't read that card. Please check the details and try again. | OK |  | `backend/routes/payments.py:1046` |
| ☐ | HTTP 400 | We couldn't read that request. Please check your connection and try again. | OK |  | `backend/routes/payments.py:998` |
| ☐ | HTTP 400 | We couldn't read those card details. Please try adding the card again. | OK |  | `backend/routes/payments.py:1009` |
| ☐ | HTTP 409 | You need at least one card on file. Add another card before removing this one. | OK |  | `backend/routes/payments.py:1250` |
| ☐ | HTTP 409 | _not_payable_message(_ride_status) | OK |  | `backend/routes/rides/payments.py:409` |
| ☐ | HTTP 402 | {'code': 'AMOUNT_MISMATCH', 'message': 'Payment amount does not cover the fare.'} | OK |  | `backend/routes/payments.py:738` |
| ☐ | HTTP 409 | {'code': 'invoice_issued', 'message': 'An invoice has been emailed for this ride. Please pay using the link in your email.'} | OK |  | `backend/routes/rides/payments.py:462` |

### `privacy-settings`  (10)

| ✓ | Trigger | Message the user sees | Verdict | Issue | Source |
|---|---|---|---|---|---|
| ☐ | showToast | Could not delete account. Please contact support. | OK |  | `rider-app/app/privacy-settings.tsx:135` |
| ☐ | showToast | Could not request your data export. Please try again. | OK |  | `rider-app/app/privacy-settings.tsx:114` |
| ☐ | showToast | Could not save your preference. Please try again. | OK |  | `rider-app/app/privacy-settings.tsx:62` |
| ☐ | showToast | Could not save your preference. Please try again. | OK |  | `rider-app/app/privacy-settings.tsx:97` |
| ☐ | showToast | Delete Failed | OK |  | `rider-app/app/privacy-settings.tsx:135` |
| ☐ | showToast | Export Failed | OK |  | `rider-app/app/privacy-settings.tsx:114` |
| ☐ | showToast | Permissions Required | OK |  | `rider-app/app/privacy-settings.tsx:52` |
| ☐ | showToast | Push notifications are disabled in device settings. Tap to open Settings. | OK |  | `rider-app/app/privacy-settings.tsx:52` |
| ☐ | showToast | Update Failed | OK |  | `rider-app/app/privacy-settings.tsx:62` |
| ☐ | showToast | Update Failed | OK |  | `rider-app/app/privacy-settings.tsx:97` |

### `profile-setup`  (16)

| ✓ | Trigger | Message the user sees | Verdict | Issue | Source |
|---|---|---|---|---|---|
| ☐ | showToast | Camera access is needed. | OK |  | `rider-app/app/profile-setup.tsx:61` |
| ☐ | showToast | Could not upload your photo. Please try again. | OK |  | `rider-app/app/profile-setup.tsx:79` |
| ☐ | Alert.alert | Discard sign-up? | OK |  | `rider-app/app/profile-setup.tsx:181` |
| ☐ | Alert.alert | Keep editing | OK |  | `rider-app/app/profile-setup.tsx:181` |
| ☐ | showToast | Library access is needed. | OK |  | `rider-app/app/profile-setup.tsx:68` |
| ☐ | showToast | Permission Denied | OK |  | `rider-app/app/profile-setup.tsx:61` |
| ☐ | showToast | Permission Denied | OK |  | `rider-app/app/profile-setup.tsx:68` |
| ☐ | showToast | Photo Updated | OK |  | `rider-app/app/profile-setup.tsx:77` |
| ☐ | showToast | Referral applied | OK |  | `rider-app/app/profile-setup.tsx:130` |
| ☐ | showToast | Referral code | OK |  | `rider-app/app/profile-setup.tsx:123` |
| ☐ | Alert.alert | Sign out | OK |  | `rider-app/app/profile-setup.tsx:181` |
| ☐ | showToast | That code couldn't be applied. | OK |  | `rider-app/app/profile-setup.tsx:123` |
| ☐ | showToast | Upload Failed | OK |  | `rider-app/app/profile-setup.tsx:79` |
| ☐ | Alert.alert | You need to finish your profile to use Spinr. Going back will sign you out. | OK |  | `rider-app/app/profile-setup.tsx:181` |
| ☐ | showToast | Your profile photo has been updated. | OK |  | `rider-app/app/profile-setup.tsx:77` |
| ☐ | showToast | Your referral code was added. | OK |  | `rider-app/app/profile-setup.tsx:130` |

### `promotions`  (29)

| ✓ | Trigger | Message the user sees | Verdict | Issue | Source |
|---|---|---|---|---|---|
| ☐ | showToast | ${res.data.discount_type === 'percentage' ? | FLAG | code identifier: discount_type | `rider-app/app/promotions.tsx:55` |
| ☐ | HTTP 400 | Choose a discount type: a flat amount or a percentage. | OK |  | `backend/routes/promotions.py:708` |
| ☐ | showToast | Couldn't Apply Code | OK |  | `rider-app/app/promotions.tsx:61` |
| ☐ | HTTP 403 | ERR_FORBIDDEN | FLAG | constant: ERR_FORBIDDEN | `backend/routes/promotions.py:419` |
| ☐ | HTTP 400 | Flat discount cannot exceed $500 | OK |  | `backend/routes/promotions.py:713` |
| ☐ | HTTP 404 | Invalid promo code | OK |  | `backend/routes/promotions.py:159` |
| ☐ | HTTP 400 | Minimum ride fare of ${min_fare} required for this promo | OK |  | `backend/routes/promotions.py:215` |
| ☐ | HTTP 400 | Percentage discount cannot exceed 100% | OK |  | `backend/routes/promotions.py:711` |
| ☐ | showToast | Promo Valid! | OK |  | `rider-app/app/promotions.tsx:55` |
| ☐ | HTTP 400 | Promo code '{code}' already exists{area_label} | OK |  | `backend/routes/promotions.py:704` |
| ☐ | HTTP 409 | Promo code has been fully redeemed | OK |  | `backend/routes/promotions.py:356` |
| ☐ | HTTP 404 | Promo code not found | OK |  | `backend/routes/promotions.py:760` |
| ☐ | HTTP 404 | Ride not found | OK |  | `backend/routes/promotions.py:417` |
| ☐ | HTTP 404 | Ride not found | OK |  | `backend/routes/promotions.py:207` |
| ☐ | HTTP 400 | This promo code has expired | OK |  | `backend/routes/promotions.py:172` |
| ☐ | HTTP 400 | This promo code has reached its usage limit | OK |  | `backend/routes/promotions.py:180` |
| ☐ | HTTP 400 | This promo code is no longer active | OK |  | `backend/routes/promotions.py:162` |
| ☐ | HTTP 400 | This promo code is not available for your account | OK |  | `backend/routes/promotions.py:223` |
| ☐ | showToast | This promo code is not valid. | OK |  | `rider-app/app/promotions.tsx:61` |
| ☐ | HTTP 400 | This promo is for first-time riders only | OK |  | `backend/routes/promotions.py:231` |
| ☐ | HTTP 400 | This promo is for new users only | OK |  | `backend/routes/promotions.py:241` |
| ☐ | HTTP 400 | This promo is for new users only | OK |  | `backend/routes/promotions.py:245` |
| ☐ | HTTP 400 | This promo is for returning riders who haven't ridden recently | OK |  | `backend/routes/promotions.py:261` |
| ☐ | HTTP 400 | This promo is not available for your ride count | OK |  | `backend/routes/promotions.py:279` |
| ☐ | HTTP 400 | This promotion has reached its budget limit | OK |  | `backend/routes/promotions.py:287` |
| ☐ | HTTP 400 | You have already used this promo code the maximum number of times | OK |  | `backend/routes/promotions.py:335` |
| ☐ | HTTP 400 | You have already used this promo code the maximum number of times | OK |  | `backend/routes/promotions.py:193` |
| ☐ | HTTP 400 | You need at least {min_rides} completed rides to use this promo | OK |  | `backend/routes/promotions.py:274` |
| ☐ | showToast | } — will apply on your next ride. | OK |  | `rider-app/app/promotions.tsx:55` |

### `queries`  (1)

| ✓ | Trigger | Message the user sees | Verdict | Issue | Source |
|---|---|---|---|---|---|
| ☐ | HTTP 403 | Not authorized to view this ride | OK |  | `backend/routes/rides/queries.py:361` |

### `rating`  (5)

| ✓ | Trigger | Message the user sees | Verdict | Issue | Source |
|---|---|---|---|---|---|
| ☐ | HTTP 400 | Imported historical rides cannot be rated | OK |  | `backend/routes/rides/rating.py:66` |
| ☐ | HTTP 409 | Ride already rated | OK |  | `backend/routes/rides/rating.py:59` |
| ☐ | HTTP 400 | Ride must be completed before rating | OK |  | `backend/routes/rides/rating.py:56` |
| ☐ | HTTP 404 | Ride not found or unauthorized | OK |  | `backend/routes/rides/rating.py:53` |
| ☐ | HTTP 400 | Tip cannot be added after payment has been settled | OK |  | `backend/routes/rides/rating.py:70` |

### `reactivate-account`  (8)

| ✓ | Trigger | Message the user sees | Verdict | Issue | Source |
|---|---|---|---|---|---|
| ☐ | showToast | Account Deleted | OK |  | `rider-app/app/reactivate-account.tsx:73` |
| ☐ | showToast | Could not reactivate. Please try again. | OK |  | `rider-app/app/reactivate-account.tsx:76` |
| ☐ | showToast | Missing reactivation token. Please sign in again. | OK |  | `rider-app/app/reactivate-account.tsx:51` |
| ☐ | showToast | Reactivation Failed | OK |  | `rider-app/app/reactivate-account.tsx:51` |
| ☐ | showToast | Reactivation Failed | OK |  | `rider-app/app/reactivate-account.tsx:76` |
| ☐ | showToast | This account has been permanently deleted. Please create a new one. | OK |  | `rider-app/app/reactivate-account.tsx:73` |
| ☐ | showToast | Welcome back | OK |  | `rider-app/app/reactivate-account.tsx:68` |
| ☐ | showToast | Your account has been reactivated. | OK |  | `rider-app/app/reactivate-account.tsx:68` |

### `receipts`  (9)

| ✓ | Trigger | Message the user sees | Verdict | Issue | Source |
|---|---|---|---|---|---|
| ☐ | HTTP 403 | Not authorized | OK |  | `backend/routes/rides/receipts.py:248` |
| ☐ | HTTP 403 | Not authorized to view this receipt | OK |  | `backend/routes/rides/receipts.py:45` |
| ☐ | HTTP 403 | Not authorized to view this receipt | OK |  | `backend/routes/rides/receipts.py:191` |
| ☐ | HTTP 400 | Receipts are only available for completed or cancelled rides | OK |  | `backend/routes/rides/receipts.py:48` |
| ☐ | HTTP 400 | Receipts are only available for completed or cancelled rides | OK |  | `backend/routes/rides/receipts.py:194` |
| ☐ | HTTP 400 | Receipts are only available for completed rides | OK |  | `backend/routes/rides/receipts.py:250` |
| ☐ | HTTP 404 | Ride not found | OK |  | `backend/routes/rides/receipts.py:42` |
| ☐ | HTTP 404 | Ride not found | OK |  | `backend/routes/rides/receipts.py:188` |
| ☐ | HTTP 404 | Ride not found | OK |  | `backend/routes/rides/receipts.py:246` |

### `referral`  (4)

| ✓ | Trigger | Message the user sees | Verdict | Issue | Source |
|---|---|---|---|---|---|
| ☐ | showToast | Copied! | OK |  | `rider-app/app/referral.tsx:85` |
| ☐ | showToast | Copied! | OK |  | `rider-app/app/referral.tsx:97` |
| ☐ | showToast | Referral code copied to clipboard | OK |  | `rider-app/app/referral.tsx:85` |
| ☐ | showToast | Referral code copied to clipboard | OK |  | `rider-app/app/referral.tsx:97` |

### `report-safety`  (6)

| ✓ | Trigger | Message the user sees | Verdict | Issue | Source |
|---|---|---|---|---|---|
| ☐ | showToast | Description Required | OK |  | `rider-app/app/report-safety.tsx:30` |
| ☐ | showToast | Failed to submit report. Please try again. | OK |  | `rider-app/app/report-safety.tsx:45` |
| ☐ | showToast | Please describe the safety issue before submitting. | OK |  | `rider-app/app/report-safety.tsx:30` |
| ☐ | showToast | Report Submitted | OK |  | `rider-app/app/report-safety.tsx:37` |
| ☐ | showToast | Submit Failed | OK |  | `rider-app/app/report-safety.tsx:45` |
| ☐ | showToast | Your safety report has been submitted. Our trust and safety team will review it immediately. | OK |  | `rider-app/app/report-safety.tsx:37` |

### `ride-completed`  (12)

| ✓ | Trigger | Message the user sees | Verdict | Issue | Source |
|---|---|---|---|---|---|
| ☐ | showToast | Could not send receipt email. Please try again. | OK |  | `rider-app/app/ride-completed.tsx:239` |
| ☐ | showToast | Could not submit report. Please try again. | OK |  | `rider-app/app/ride-completed.tsx:261` |
| ☐ | showToast | Email Not Sent | OK |  | `rider-app/app/ride-completed.tsx:239` |
| ☐ | showToast | Failed to submit. Please try again. | OK |  | `rider-app/app/ride-completed.tsx:417` |
| ☐ | showToast | Payment Failed | OK |  | `rider-app/app/ride-completed.tsx:463` |
| ☐ | showToast | Please try again. | OK |  | `rider-app/app/ride-completed.tsx:463` |
| ☐ | showToast | Receipt Sent | OK |  | `rider-app/app/ride-completed.tsx:237` |
| ☐ | showToast | Receipt emailed to ${user?.email \|\| 'your registered email'}. | OK |  | `rider-app/app/ride-completed.tsx:237` |
| ☐ | showToast | Report Not Sent | OK |  | `rider-app/app/ride-completed.tsx:261` |
| ☐ | showToast | Report Submitted | OK |  | `rider-app/app/ride-completed.tsx:259` |
| ☐ | showToast | Submit Failed | OK |  | `rider-app/app/ride-completed.tsx:417` |
| ☐ | showToast | Your driver has been notified. They'll get back to you if the item is found. | OK |  | `rider-app/app/ride-completed.tsx:259` |

### `ride-details`  (9)

| ✓ | Trigger | Message the user sees | Verdict | Issue | Source |
|---|---|---|---|---|---|
| ☐ | showToast | Could not download the receipt. Please check your connection and try again. | OK |  | `rider-app/app/ride-details.tsx:97` |
| ☐ | showToast | Could not send receipt email. Please try again. | OK |  | `rider-app/app/ride-details.tsx:59` |
| ☐ | showToast | Download Failed | OK |  | `rider-app/app/ride-details.tsx:97` |
| ☐ | showToast | Email Not Sent | OK |  | `rider-app/app/ride-details.tsx:59` |
| ☐ | showToast | PDF Unavailable | OK |  | `rider-app/app/ride-details.tsx:76` |
| ☐ | showToast | PDF export requires the latest app version. Please update the app and try again. | OK |  | `rider-app/app/ride-details.tsx:76` |
| ☐ | showToast | Receipt PDF downloaded. | OK |  | `rider-app/app/ride-details.tsx:94` |
| ☐ | showToast | Receipt Sent | OK |  | `rider-app/app/ride-details.tsx:57` |
| ☐ | showToast | Receipt emailed to ${user?.email \|\| 'your registered email'}. | OK |  | `rider-app/app/ride-details.tsx:57` |

### `ride-in-progress`  (12)

| ✓ | Trigger | Message the user sees | Verdict | Issue | Source |
|---|---|---|---|---|---|
| ☐ | showToast | Copied! | OK |  | `rider-app/app/ride-in-progress.tsx:498` |
| ☐ | showToast | Could not end ride. Please try again. | OK |  | `rider-app/app/ride-in-progress.tsx:410` |
| ☐ | showToast | Could not end ride. Please try again. | OK |  | `rider-app/app/ride-in-progress.tsx:683` |
| ☐ | showToast | Live tracking link copied to clipboard. | OK |  | `rider-app/app/ride-in-progress.tsx:498` |
| ☐ | showToast | Live trip tracking is not set up yet. Please contact support. | OK |  | `rider-app/app/ride-in-progress.tsx:433` |
| ☐ | showToast | Live trip tracking is not set up yet. Please contact support. | OK |  | `rider-app/app/ride-in-progress.tsx:480` |
| ☐ | showToast | Share Failed | OK |  | `rider-app/app/ride-in-progress.tsx:474` |
| ☐ | showToast | Tracking Not Configured | OK |  | `rider-app/app/ride-in-progress.tsx:433` |
| ☐ | showToast | Tracking Not Configured | OK |  | `rider-app/app/ride-in-progress.tsx:480` |
| ☐ | showToast | Trip Shared! | OK |  | `rider-app/app/ride-in-progress.tsx:472` |
| ☐ | showToast | Unable to share trip details. Please try again. | OK |  | `rider-app/app/ride-in-progress.tsx:474` |
| ☐ | showToast | Your live location is now being shared. | OK |  | `rider-app/app/ride-in-progress.tsx:472` |

### `ride-options`  (12)

| ✓ | Trigger | Message the user sees | Verdict | Issue | Source |
|---|---|---|---|---|---|
| ☐ | showToast | Booking Failed | OK |  | `rider-app/app/ride-options.tsx:768` |
| ☐ | showToast | Card authentication needed | OK |  | `rider-app/app/ride-options.tsx:703` |
| ☐ | showToast | Failed to book ride. Please try again. | OK |  | `rider-app/app/ride-options.tsx:768` |
| ☐ | showToast | Invalid Time | OK |  | `rider-app/app/ride-options.tsx:647` |
| ☐ | showToast | Not eligible | OK |  | `rider-app/app/ride-options.tsx:806` |
| ☐ | showToast | Not eligible | OK |  | `rider-app/app/ride-options.tsx:1440` |
| ☐ | showToast | Promo not applied | OK |  | `rider-app/app/ride-options.tsx:707` |
| ☐ | showToast | Scheduled time must be at least 15 minutes from now. | OK |  | `rider-app/app/ride-options.tsx:647` |
| ☐ | showToast | This card needs extra verification. Please pick another payment method or try again. | OK |  | `rider-app/app/ride-options.tsx:703` |
| ☐ | showToast | This promo cannot be applied to this ride | OK |  | `rider-app/app/ride-options.tsx:806` |
| ☐ | showToast | This promo cannot be applied to this ride | OK |  | `rider-app/app/ride-options.tsx:1440` |
| ☐ | showToast | This vehicle type is not available right now. | OK |  | `rider-app/app/ride-options.tsx:602` |

### `ride-status`  (3)

| ✓ | Trigger | Message the user sees | Verdict | Issue | Source |
|---|---|---|---|---|---|
| ☐ | showToast | Could not cancel | OK |  | `rider-app/app/ride-status.tsx:135` |
| ☐ | showToast | Note not saved | OK |  | `rider-app/app/ride-status.tsx:663` |
| ☐ | showToast | The server rejected the request. Please try again. | OK |  | `rider-app/app/ride-status.tsx:135` |

### `ride-tracking-webview`  (3)

| ✓ | Trigger | Message the user sees | Verdict | Issue | Source |
|---|---|---|---|---|---|
| ☐ | setError | Could not generate tracking link. Please try again. | OK |  | `rider-app/app/ride-tracking-webview.tsx:100` |
| ☐ | setError | Could not load tracking page. | OK |  | `rider-app/app/ride-tracking-webview.tsx:181` |
| ☐ | setError | Live trip tracking is not set up yet. Please contact support. | OK |  | `rider-app/app/ride-tracking-webview.tsx:90` |

### `rideStore`  (4)

| ✓ | Trigger | Message the user sees | Verdict | Issue | Source |
|---|---|---|---|---|---|
| ☐ | showToast | ${failedPermanently.length} offline action(s) could not be synced. | OK |  | `rider-app/store/rideStore.ts:698` |
| ☐ | showToast | Offline Actions Lost | OK |  | `rider-app/store/rideStore.ts:652` |
| ☐ | showToast | Some queued actions could not be recovered. Please rebook if needed. | OK |  | `rider-app/store/rideStore.ts:652` |
| ☐ | showToast | Sync Failed | OK |  | `rider-app/store/rideStore.ts:698` |

### `safety`  (5)

| ✓ | Trigger | Message the user sees | Verdict | Issue | Source |
|---|---|---|---|---|---|
| ☐ | HTTP 403 | Not authorized to trigger emergency for this ride | OK |  | `backend/routes/rides/safety.py:113` |
| ☐ | HTTP 404 | Not found | OK |  | `backend/routes/rides/safety.py:541` |
| ☐ | HTTP 409 | Ride is not in progress | OK |  | `backend/routes/rides/safety.py:844` |
| ☐ | HTTP 404 | Ride not found | OK |  | `backend/routes/rides/safety.py:105` |
| ☐ | HTTP 404 | Ride not found | OK |  | `backend/routes/rides/safety.py:842` |

### `saved-places`  (4)

| ✓ | Trigger | Message the user sees | Verdict | Issue | Source |
|---|---|---|---|---|---|
| ☐ | showToast | Address Required | OK |  | `rider-app/app/saved-places.tsx:104` |
| ☐ | showToast | Could not save this place. Please try again. | OK |  | `rider-app/app/saved-places.tsx:118` |
| ☐ | showToast | Please search for and select an address | OK |  | `rider-app/app/saved-places.tsx:104` |
| ☐ | showToast | Save Failed | OK |  | `rider-app/app/saved-places.tsx:118` |

### `scheduled-rides`  (3)

| ✓ | Trigger | Message the user sees | Verdict | Issue | Source |
|---|---|---|---|---|---|
| ☐ | showToast | Cancel Failed | OK |  | `rider-app/app/scheduled-rides.tsx:81` |
| ☐ | showToast | Failed to cancel your scheduled ride. Please try again. | OK |  | `rider-app/app/scheduled-rides.tsx:81` |
| ☐ | showToast | Your scheduled ride has been cancelled. | OK |  | `rider-app/app/scheduled-rides.tsx:79` |

### `search-destination`  (4)

| ✓ | Trigger | Message the user sees | Verdict | Issue | Source |
|---|---|---|---|---|---|
| ☐ | showToast | No Home Address | OK |  | `rider-app/app/search-destination.tsx:672` |
| ☐ | showToast | No Work Address | OK |  | `rider-app/app/search-destination.tsx:694` |
| ☐ | showToast | Set your home address in Account > Saved Places | OK |  | `rider-app/app/search-destination.tsx:672` |
| ☐ | showToast | Set your work address in Account > Saved Places | OK |  | `rider-app/app/search-destination.tsx:694` |

### `settings`  (2)

| ✓ | Trigger | Message the user sees | Verdict | Issue | Source |
|---|---|---|---|---|---|
| ☐ | showToast | Permissions Required | OK |  | `rider-app/app/settings.tsx:65` |
| ☐ | showToast | Push notifications are disabled in device settings. Tap to open Settings. | OK |  | `rider-app/app/settings.tsx:65` |

### `sharing`  (13)

| ✓ | Trigger | Message the user sees | Verdict | Issue | Source |
|---|---|---|---|---|---|
| ☐ | HTTP 400 | Cannot share a completed or cancelled ride | OK |  | `backend/routes/rides/sharing.py:58` |
| ☐ | HTTP 400 | Cannot share a completed or cancelled ride | OK |  | `backend/routes/rides/sharing.py:141` |
| ☐ | HTTP 403 | Not authorized | OK |  | `backend/routes/rides/sharing.py:139` |
| ☐ | HTTP 403 | Not authorized | OK |  | `backend/routes/rides/sharing.py:217` |
| ☐ | HTTP 403 | Not authorized | OK |  | `backend/routes/rides/sharing.py:342` |
| ☐ | HTTP 403 | Not authorized to share this ride | OK |  | `backend/routes/rides/sharing.py:55` |
| ☐ | HTTP 404 | Ride not found | OK |  | `backend/routes/rides/sharing.py:46` |
| ☐ | HTTP 404 | Ride not found | OK |  | `backend/routes/rides/sharing.py:137` |
| ☐ | HTTP 404 | Ride not found | OK |  | `backend/routes/rides/sharing.py:215` |
| ☐ | HTTP 404 | Ride not found | OK |  | `backend/routes/rides/sharing.py:340` |
| ☐ | HTTP 404 | Share link has expired | OK |  | `backend/routes/rides/sharing.py:253` |
| ☐ | HTTP 404 | Share link has expired | OK |  | `backend/routes/rides/sharing.py:238` |
| ☐ | HTTP 404 | Shared ride not found or link expired | OK |  | `backend/routes/rides/sharing.py:228` |

### `stops`  (10)

| ✓ | Trigger | Message the user sees | Verdict | Issue | Source |
|---|---|---|---|---|---|
| ☐ | HTTP 400 | Can only edit stops on an active ride | OK |  | `backend/routes/rides/stops.py:59` |
| ☐ | HTTP 400 | Can only edit stops on an active ride | OK |  | `backend/routes/rides/stops.py:119` |
| ☐ | HTTP 400 | Invalid stop index | OK |  | `backend/routes/rides/stops.py:123` |
| ☐ | HTTP 403 | Not authorized | OK |  | `backend/routes/rides/stops.py:53` |
| ☐ | HTTP 403 | Not authorized | OK |  | `backend/routes/rides/stops.py:113` |
| ☐ | HTTP 403 | Not authorized | OK |  | `backend/routes/rides/stops.py:182` |
| ☐ | HTTP 409 | Notes can only be edited before pickup | OK |  | `backend/routes/rides/stops.py:190` |
| ☐ | HTTP 404 | Ride not found | OK |  | `backend/routes/rides/stops.py:51` |
| ☐ | HTTP 404 | Ride not found | OK |  | `backend/routes/rides/stops.py:111` |
| ☐ | HTTP 404 | Ride not found | OK |  | `backend/routes/rides/stops.py:180` |

### `tracking`  (2)

| ✓ | Trigger | Message the user sees | Verdict | Issue | Source |
|---|---|---|---|---|---|
| ☐ | HTTP 403 | Not authorized to view this ride | OK |  | `backend/routes/rides/tracking.py:73` |
| ☐ | HTTP 403 | Not authorized to view this ride | OK |  | `backend/routes/rides/tracking.py:225` |

### `useRiderSocket`  (4)

| ✓ | Trigger | Message the user sees | Verdict | Issue | Source |
|---|---|---|---|---|---|
| ☐ | showToast | Driver Unavailable | OK |  | `rider-app/hooks/useRiderSocket.ts:166` |
| ☐ | showToast | Ride Cancelled | OK |  | `rider-app/hooks/useRiderSocket.ts:152` |
| ☐ | showToast | The driver did not respond in time. Finding another driver\u2026 | OK |  | `rider-app/hooks/useRiderSocket.ts:166` |
| ☐ | showToast | Your ride has been cancelled. | OK |  | `rider-app/hooks/useRiderSocket.ts:152` |

### `verify-email`  (21)

| ✓ | Trigger | Message the user sees | Verdict | Issue | Source |
|---|---|---|---|---|---|
| ☐ | showToast | Add an email to your profile before verifying it. | OK |  | `rider-app/app/verify-email.tsx:86` |
| ☐ | showToast | Already Verified | OK |  | `rider-app/app/verify-email.tsx:125` |
| ☐ | showToast | Check Your Email | OK |  | `rider-app/app/verify-email.tsx:130` |
| ☐ | showToast | Code Sent | OK |  | `rider-app/app/verify-email.tsx:130` |
| ☐ | showToast | Could Not Send Code | OK |  | `rider-app/app/verify-email.tsx:151` |
| ☐ | showToast | Email Verified | OK |  | `rider-app/app/verify-email.tsx:197` |
| ☐ | showToast | Invalid Code | OK |  | `rider-app/app/verify-email.tsx:183` |
| ☐ | showToast | No Email on File | OK |  | `rider-app/app/verify-email.tsx:86` |
| ☐ | showToast | Please enter the code sent to your email. | OK |  | `rider-app/app/verify-email.tsx:183` |
| ☐ | showToast | Please wait before requesting another code. | OK |  | `rider-app/app/verify-email.tsx:142` |
| ☐ | showToast | Request failed | OK |  | `rider-app/app/verify-email.tsx:142` |
| ☐ | showToast | Request failed | OK |  | `rider-app/app/verify-email.tsx:204` |
| ☐ | showToast | That code did not work. Please try again. | OK |  | `rider-app/app/verify-email.tsx:213` |
| ☐ | showToast | Too Many Attempts | OK |  | `rider-app/app/verify-email.tsx:142` |
| ☐ | showToast | Too Many Attempts | OK |  | `rider-app/app/verify-email.tsx:204` |
| ☐ | showToast | Too many failed attempts. Please wait before trying again. | OK |  | `rider-app/app/verify-email.tsx:204` |
| ☐ | showToast | Verification Failed | OK |  | `rider-app/app/verify-email.tsx:213` |
| ☐ | showToast | We could not send a verification code. Please try again. | OK |  | `rider-app/app/verify-email.tsx:151` |
| ☐ | showToast | We sent a verification code to ${email}. It expires in ${OTP_EXPIRY_MINUTES} minutes. | OK |  | `rider-app/app/verify-email.tsx:130` |
| ☐ | showToast | Your email has been verified. | OK |  | `rider-app/app/verify-email.tsx:197` |
| ☐ | showToast | Your email is already verified. | OK |  | `rider-app/app/verify-email.tsx:125` |

### `wallet`  (19)

| ✓ | Trigger | Message the user sees | Verdict | Issue | Source |
|---|---|---|---|---|---|
| ☐ | showToast | $${effectiveAmount.toFixed(2)} will be added to your wallet shortly. | OK |  | `rider-app/app/wallet.tsx:130` |
| ☐ | showToast | Could not complete your top-up. Please try again. | OK |  | `rider-app/app/wallet.tsx:133` |
| ☐ | HTTP 400 | ERR_FARE_EXCEEDED | FLAG | constant: ERR_FARE_EXCEEDED | `backend/routes/wallet.py:249` |
| ☐ | HTTP 400 | ERR_FARE_UNDERPAID | FLAG | constant: ERR_FARE_UNDERPAID | `backend/routes/wallet.py:251` |
| ☐ | HTTP 400 | ERR_FARE_UNDERPAID | FLAG | constant: ERR_FARE_UNDERPAID | `backend/routes/wallet.py:266` |
| ☐ | HTTP 403 | ERR_FORBIDDEN | FLAG | constant: ERR_FORBIDDEN | `backend/routes/wallet.py:243` |
| ☐ | HTTP 400 | ERR_RIDE_NOT_PAYABLE | FLAG | constant: ERR_RIDE_NOT_PAYABLE | `backend/routes/wallet.py:245` |
| ☐ | HTTP 400 | ERR_RIDE_NOT_PAYABLE | FLAG | constant: ERR_RIDE_NOT_PAYABLE | `backend/routes/wallet.py:268` |
| ☐ | showToast | Invalid Amount | OK |  | `rider-app/app/wallet.tsx:93` |
| ☐ | showToast | Payment Error | OK |  | `rider-app/app/wallet.tsx:115` |
| ☐ | showToast | Payment Failed | OK |  | `rider-app/app/wallet.tsx:122` |
| ☐ | showToast | Payment Not Available | OK |  | `rider-app/app/wallet.tsx:89` |
| ☐ | showToast | Payment Successful | OK |  | `rider-app/app/wallet.tsx:130` |
| ☐ | showToast | Payment processing is not set up yet. Please add a card in Cards settings first, or try again later. | OK |  | `rider-app/app/wallet.tsx:89` |
| ☐ | showToast | Please select or enter an amount between $1 and $500. | OK |  | `rider-app/app/wallet.tsx:93` |
| ☐ | HTTP 404 | Ride not found | OK |  | `backend/routes/wallet.py:241` |
| ☐ | showToast | Top-up Failed | OK |  | `rider-app/app/wallet.tsx:133` |
| ☐ | HTTP 403 | Your wallet is suspended | OK |  | `backend/routes/wallet.py:153` |
| ☐ | HTTP 403 | Your wallet is suspended | OK |  | `backend/routes/wallet.py:234` |

### `work-allowance-request`  (6)

| ✓ | Trigger | Message the user sees | Verdict | Issue | Source |
|---|---|---|---|---|---|
| ☐ | showToast | $${parsedAmount.toFixed(2)} has been added to your allowance automatically. | OK |  | `rider-app/app/work-allowance-request.tsx:55` |
| ☐ | showToast | Auto-approved! | OK |  | `rider-app/app/work-allowance-request.tsx:55` |
| ☐ | showToast | Could not submit your request. Please try again. | OK |  | `rider-app/app/work-allowance-request.tsx:65` |
| ☐ | showToast | Request Failed | OK |  | `rider-app/app/work-allowance-request.tsx:65` |
| ☐ | showToast | Request Submitted | OK |  | `rider-app/app/work-allowance-request.tsx:55` |
| ☐ | showToast | Your request has been sent to your company admin for review. | OK |  | `rider-app/app/work-allowance-request.tsx:55` |

### `work-profile`  (3)

| ✓ | Trigger | Message the user sees | Verdict | Issue | Source |
|---|---|---|---|---|---|
| ☐ | showToast | Could not generate your statement. Please try again. | OK |  | `rider-app/app/work-profile.tsx:220` |
| ☐ | showToast | Statement PDF generated. | OK |  | `rider-app/app/work-profile.tsx:217` |
| ☐ | showToast | Statement Unavailable | OK |  | `rider-app/app/work-profile.tsx:220` |

---

## DRIVER APP  (469 messages)

### `RideOfferPanel`  (3)

| ✓ | Trigger | Message the user sees | Verdict | Issue | Source |
|---|---|---|---|---|---|
| ☐ | AlertDialog | If you're declining because you can't accommodate a service animal, let us know. Service animal accommodation is mandatory and refusals are reviewed. | OK |  | `driver-app/components/panels/RideOfferPanel.tsx:186` |
| ☐ | AlertDialog | Report a reason? | OK |  | `driver-app/components/panels/RideOfferPanel.tsx:186` |
| ☐ | AlertDialog | Service animal — report & decline | OK |  | `driver-app/components/panels/RideOfferPanel.tsx:186` |

### `_shared`  (4)

| ✓ | Trigger | Message the user sees | Verdict | Issue | Source |
|---|---|---|---|---|---|
| ☐ | HTTP 404 | Driver not found | OK |  | `backend/routes/drivers/_shared.py:912` |
| ☐ | HTTP 404 | Ride not found | OK |  | `backend/routes/drivers/_shared.py:896` |
| ☐ | HTTP 429 | Too many incorrect pickup codes for this ride — try again later | OK |  | `backend/routes/drivers/_shared.py:385` |
| ☐ | HTTP 409 | unavailable_message(driver_phrase(current)) | FLAG | code identifier: driver_phrase, unavailable_message | `backend/routes/drivers/_shared.py:892` |

### `addresses`  (20)

| ✓ | Trigger | Message the user sees | Verdict | Issue | Source |
|---|---|---|---|---|---|
| ☐ | Alert.alert | Address Deleted | OK |  | `driver-app/app/driver/addresses.tsx:104` |
| ☐ | showToast | Address Deleted | OK |  | `driver-app/app/driver/addresses.tsx:113` |
| ☐ | showToast | Address Saved | OK |  | `driver-app/app/driver/addresses.tsx:148` |
| ☐ | Alert.alert | Address has been removed. | OK |  | `driver-app/app/driver/addresses.tsx:104` |
| ☐ | showToast | Address has been removed. | OK |  | `driver-app/app/driver/addresses.tsx:113` |
| ☐ | showToast | Address has been saved. | OK |  | `driver-app/app/driver/addresses.tsx:148` |
| ☐ | showToast | Address not found | OK |  | `driver-app/app/driver/addresses.tsx:133` |
| ☐ | Alert.alert | Are you sure you want to delete this address? | OK |  | `driver-app/app/driver/addresses.tsx:104` |
| ☐ | Alert.alert | Could not delete the address. Please try again. | OK |  | `driver-app/app/driver/addresses.tsx:104` |
| ☐ | showToast | Could not delete the address. Please try again. | OK |  | `driver-app/app/driver/addresses.tsx:115` |
| ☐ | showToast | Could not load your saved addresses. Please try again. | OK |  | `driver-app/app/driver/addresses.tsx:90` |
| ☐ | showToast | Could not save your address. Please try again. | OK |  | `driver-app/app/driver/addresses.tsx:150` |
| ☐ | Alert.alert | Delete Address | OK |  | `driver-app/app/driver/addresses.tsx:104` |
| ☐ | Alert.alert | Delete Failed | OK |  | `driver-app/app/driver/addresses.tsx:104` |
| ☐ | showToast | Delete Failed | OK |  | `driver-app/app/driver/addresses.tsx:115` |
| ☐ | showToast | Load Failed | OK |  | `driver-app/app/driver/addresses.tsx:90` |
| ☐ | showToast | Missing Fields | OK |  | `driver-app/app/driver/addresses.tsx:124` |
| ☐ | showToast | Please fill in both fields | OK |  | `driver-app/app/driver/addresses.tsx:124` |
| ☐ | showToast | Save Failed | OK |  | `driver-app/app/driver/addresses.tsx:150` |
| ☐ | showToast | We could not locate that address on the map. Please enter a more specific address (include city/province). | OK |  | `driver-app/app/driver/addresses.tsx:133` |

### `appeal`  (6)

| ✓ | Trigger | Message the user sees | Verdict | Issue | Source |
|---|---|---|---|---|---|
| ☐ | showToast | Appeal submitted | OK |  | `driver-app/app/appeal.tsx:82` |
| ☐ | showToast | Could not load | OK |  | `driver-app/app/appeal.tsx:62` |
| ☐ | showToast | Could not submit | OK |  | `driver-app/app/appeal.tsx:89` |
| ☐ | showToast | Please check your connection and try again. | OK |  | `driver-app/app/appeal.tsx:62` |
| ☐ | showToast | Please try again. | OK |  | `driver-app/app/appeal.tsx:89` |
| ☐ | showToast | We'll review it and get back to you. | OK |  | `driver-app/app/appeal.tsx:82` |

### `appeals`  (3)

| ✓ | Trigger | Message the user sees | Verdict | Issue | Source |
|---|---|---|---|---|---|
| ☐ | HTTP 409 | You already have a pending appeal. We'll respond to that one before you can submit another. | OK |  | `backend/routes/drivers/appeals.py:74` |
| ☐ | HTTP 400 | Your account isn't currently suspended, banned, or under review — there's nothing to appeal. | OK |  | `backend/routes/drivers/appeals.py:60` |
| ☐ | HTTP 400 | str(e) | OK |  | `backend/routes/drivers/appeals.py:79` |

### `become-driver`  (23)

| ✓ | Trigger | Message the user sees | Verdict | Issue | Source |
|---|---|---|---|---|---|
| ☐ | Alert.alert | Application submitted! You can now log in, but you must complete your profile to go online. | OK |  | `driver-app/app/become-driver.tsx:599` |
| ☐ | Alert.alert | Camera permission is required to take photos. | OK |  | `driver-app/app/become-driver.tsx:377` |
| ☐ | Alert.alert | Choose a source | OK |  | `driver-app/app/become-driver.tsx:435` |
| ☐ | Alert.alert | Choose a source | OK |  | `driver-app/app/become-driver.tsx:447` |
| ☐ | Alert.alert | Connection Error | OK |  | `driver-app/app/become-driver.tsx:234` |
| ☐ | Alert.alert | Consent required | OK |  | `driver-app/app/become-driver.tsx:498` |
| ☐ | Alert.alert | Could not load vehicle types. Check your connection. | OK |  | `driver-app/app/become-driver.tsx:234` |
| ☐ | Alert.alert | Could not submit your application. Please try again. | OK |  | `driver-app/app/become-driver.tsx:608` |
| ☐ | Alert.alert | Could not upload your document. Please try again. | OK |  | `driver-app/app/become-driver.tsx:366` |
| ☐ | Alert.alert | Document uploaded successfully | OK |  | `driver-app/app/become-driver.tsx:364` |
| ☐ | Alert.alert | Expiry date must be in the future. | OK |  | `driver-app/app/become-driver.tsx:323` |
| ☐ | Alert.alert | Failed to pick file | OK |  | `driver-app/app/become-driver.tsx:429` |
| ☐ | Alert.alert | Failed to pick image | OK |  | `driver-app/app/become-driver.tsx:412` |
| ☐ | Alert.alert | File / Cancel | OK |  | `driver-app/app/become-driver.tsx:447` |
| ☐ | Alert.alert | Gallery permission is required to upload photos. | OK |  | `driver-app/app/become-driver.tsx:383` |
| ☐ | Alert.alert | Invalid Date | OK |  | `driver-app/app/become-driver.tsx:323` |
| ☐ | Alert.alert | Permission needed | OK |  | `driver-app/app/become-driver.tsx:377` |
| ☐ | Alert.alert | Permission needed | OK |  | `driver-app/app/become-driver.tsx:383` |
| ☐ | Alert.alert | Please read and check the Criminal Record Check / Vulnerable Sector Check consent before submitting your application. | OK |  | `driver-app/app/become-driver.tsx:498` |
| ☐ | Alert.alert | Registration Failed | OK |  | `driver-app/app/become-driver.tsx:608` |
| ☐ | Alert.alert | Upload Document | OK |  | `driver-app/app/become-driver.tsx:435` |
| ☐ | Alert.alert | Upload Document | OK |  | `driver-app/app/become-driver.tsx:447` |
| ☐ | Alert.alert | Upload Failed | OK |  | `driver-app/app/become-driver.tsx:366` |

### `crc-consent`  (6)

| ✓ | Trigger | Message the user sees | Verdict | Issue | Source |
|---|---|---|---|---|---|
| ☐ | showToast | Consent recorded | OK |  | `driver-app/app/crc-consent.tsx:72` |
| ☐ | showToast | Could not load | OK |  | `driver-app/app/crc-consent.tsx:57` |
| ☐ | showToast | Could not submit | OK |  | `driver-app/app/crc-consent.tsx:75` |
| ☐ | showToast | Please check your connection and try again. | OK |  | `driver-app/app/crc-consent.tsx:57` |
| ☐ | showToast | Please try again. | OK |  | `driver-app/app/crc-consent.tsx:75` |
| ☐ | showToast | Thank you. | OK |  | `driver-app/app/crc-consent.tsx:72` |

### `documents`  (14)

| ✓ | Trigger | Message the user sees | Verdict | Issue | Source |
|---|---|---|---|---|---|
| ☐ | showToast | Camera permission is required to take photos. | OK |  | `driver-app/app/documents.tsx:141` |
| ☐ | Alert.alert | Choose a source | OK |  | `driver-app/app/documents.tsx:201` |
| ☐ | showToast | Could not load your documents. Please try again. | OK |  | `driver-app/app/documents.tsx:75` |
| ☐ | showToast | Could not open that file. Please try again. | OK |  | `driver-app/app/documents.tsx:196` |
| ☐ | showToast | Could not upload your document. Please try again. | OK |  | `driver-app/app/documents.tsx:130` |
| ☐ | showToast | Document submitted for review. | OK |  | `driver-app/app/documents.tsx:128` |
| ☐ | showToast | Failed to pick image | OK |  | `driver-app/app/documents.tsx:175` |
| ☐ | showToast | Gallery permission is required to upload photos. | OK |  | `driver-app/app/documents.tsx:147` |
| ☐ | showToast | Load Failed | OK |  | `driver-app/app/documents.tsx:75` |
| ☐ | showToast | Permission needed | OK |  | `driver-app/app/documents.tsx:141` |
| ☐ | showToast | Permission needed | OK |  | `driver-app/app/documents.tsx:147` |
| ☐ | Alert.alert | Upload Document | OK |  | `driver-app/app/documents.tsx:201` |
| ☐ | showToast | Upload Failed | OK |  | `driver-app/app/documents.tsx:130` |
| ☐ | showToast | Upload Failed | OK |  | `driver-app/app/documents.tsx:196` |

### `earnings`  (10)

| ✓ | Trigger | Message the user sees | Verdict | Issue | Source |
|---|---|---|---|---|---|
| ☐ | HTTP 422 | Date range cannot exceed 12 months (365 days) | OK |  | `backend/routes/drivers/earnings.py:601` |
| ☐ | HTTP 404 | Driver not found | OK |  | `backend/routes/drivers/earnings.py:54` |
| ☐ | HTTP 404 | Driver not found | OK |  | `backend/routes/drivers/earnings.py:317` |
| ☐ | HTTP 404 | Driver not found | OK |  | `backend/routes/drivers/earnings.py:352` |
| ☐ | HTTP 404 | Driver not found | OK |  | `backend/routes/drivers/earnings.py:533` |
| ☐ | HTTP 404 | Driver not found | OK |  | `backend/routes/drivers/earnings.py:607` |
| ☐ | HTTP 404 | Driver not found | OK |  | `backend/routes/drivers/earnings.py:662` |
| ☐ | HTTP 404 | Driver not found | OK |  | `backend/routes/drivers/earnings.py:782` |
| ☐ | HTTP 404 | Driver not found | OK |  | `backend/routes/drivers/earnings.py:884` |
| ☐ | HTTP 404 | Driver not found | OK |  | `backend/routes/drivers/earnings.py:981` |

### `emergency-contacts`  (10)

| ✓ | Trigger | Message the user sees | Verdict | Issue | Source |
|---|---|---|---|---|---|
| ☐ | showToast | ${trimmedName} has been added as an emergency contact. | OK |  | `driver-app/app/driver/emergency-contacts.tsx:114` |
| ☐ | showToast | Contact Added | OK |  | `driver-app/app/driver/emergency-contacts.tsx:114` |
| ☐ | showToast | Could Not Add | OK |  | `driver-app/app/driver/emergency-contacts.tsx:116` |
| ☐ | showToast | Could not add contact. Please try again. | OK |  | `driver-app/app/driver/emergency-contacts.tsx:116` |
| ☐ | Alert.alert | Could not remove contact. Please try again. | OK |  | `driver-app/app/driver/emergency-contacts.tsx:123` |
| ☐ | showToast | Could not remove contact. Please try again. | OK |  | `driver-app/app/driver/emergency-contacts.tsx:136` |
| ☐ | Alert.alert | Remove ${contact.name} as an emergency contact? | OK |  | `driver-app/app/driver/emergency-contacts.tsx:123` |
| ☐ | Alert.alert | Remove Contact | OK |  | `driver-app/app/driver/emergency-contacts.tsx:123` |
| ☐ | Alert.alert | Remove Failed | OK |  | `driver-app/app/driver/emergency-contacts.tsx:123` |
| ☐ | showToast | Remove Failed | OK |  | `driver-app/app/driver/emergency-contacts.tsx:136` |

### `index`  (6)

| ✓ | Trigger | Message the user sees | Verdict | Issue | Source |
|---|---|---|---|---|---|
| ☐ | showToast | Marked as arrived at the pickup spot. | OK |  | `driver-app/app/driver/(tabs)/index.tsx:1249` |
| ☐ | showToast | Off Route | OK |  | `driver-app/app/driver/(tabs)/index.tsx:1208` |
| ☐ | showToast | Please try again. | OK |  | `driver-app/app/driver/(tabs)/index.tsx:1265` |
| ☐ | showToast | Something Went Wrong | OK |  | `driver-app/app/driver/(tabs)/index.tsx:1265` |
| ☐ | showToast | You have left the planned route. | OK |  | `driver-app/app/driver/(tabs)/index.tsx:1208` |
| ☐ | showToast | You've Arrived | OK |  | `driver-app/app/driver/(tabs)/index.tsx:1249` |

### `location`  (14)

| ✓ | Trigger | Message the user sees | Verdict | Issue | Source |
|---|---|---|---|---|---|
| ☐ | HTTP 422 | A recent position is required | OK |  | `backend/routes/drivers/location.py:767` |
| ☐ | HTTP 404 | Assigned ride not found | OK |  | `backend/routes/drivers/location.py:407` |
| ☐ | HTTP 409 | Driver is not online | OK |  | `backend/routes/drivers/location.py:331` |
| ☐ | HTTP 409 | Driver is not online | OK |  | `backend/routes/drivers/location.py:764` |
| ☐ | HTTP 403 | Driver profile required | OK |  | `backend/routes/drivers/location.py:316` |
| ☐ | HTTP 403 | Driver profile required | OK |  | `backend/routes/drivers/location.py:400` |
| ☐ | HTTP 403 | Driver profile required | OK |  | `backend/routes/drivers/location.py:761` |
| ☐ | HTTP 400 | Driver with this phone already exists | OK |  | `backend/routes/drivers/location.py:670` |
| ☐ | HTTP 401 | ERR_SESSION_REVOKED | FLAG | constant: ERR_SESSION_REVOKED | `backend/routes/drivers/location.py:728` |
| ☐ | HTTP 409 | Idle location recording is not enabled | OK |  | `backend/routes/drivers/location.py:329` |
| ☐ | HTTP 422 | Points fall outside completed ride retention window | OK |  | `backend/routes/drivers/location.py:411` |
| ☐ | HTTP 409 | Ride cannot accept location points in its current state | OK |  | `backend/routes/drivers/location.py:413` |
| ☐ | HTTP 422 | exc.errors() | OK |  | `backend/routes/drivers/location.py:290` |
| ☐ | HTTP 422 | exc.errors() | OK |  | `backend/routes/drivers/location.py:286` |

### `login`  (6)

| ✓ | Trigger | Message the user sees | Verdict | Issue | Source |
|---|---|---|---|---|---|
| ☐ | showToast | Connection Error | OK |  | `driver-app/app/login.tsx:150` |
| ☐ | showToast | Could not send verification code. Please try again. | OK |  | `driver-app/app/login.tsx:139` |
| ☐ | showToast | Invalid Number | OK |  | `driver-app/app/login.tsx:107` |
| ☐ | showToast | Please enter a valid 10-digit phone number. | OK |  | `driver-app/app/login.tsx:107` |
| ☐ | showToast | Sign-in Unavailable | OK |  | `driver-app/app/login.tsx:148` |
| ☐ | showToast | Unable to reach server. Please check your connection. | OK |  | `driver-app/app/login.tsx:150` |

### `lost-and-found-chat`  (21)

| ✓ | Trigger | Message the user sees | Verdict | Issue | Source |
|---|---|---|---|---|---|
| ☐ | Alert.alert | Add a Photo | OK |  | `driver-app/app/driver/lost-and-found-chat.tsx:261` |
| ☐ | Alert.alert | Are you sure you want to ${label}? | OK |  | `driver-app/app/driver/lost-and-found-chat.tsx:200` |
| ☐ | showToast | Camera access is needed to take a photo. | OK |  | `driver-app/app/driver/lost-and-found-chat.tsx:273` |
| ☐ | Alert.alert | Confirm Item Found | OK |  | `driver-app/app/driver/lost-and-found-chat.tsx:200` |
| ☐ | showToast | Could not load case. | OK |  | `driver-app/app/driver/lost-and-found-chat.tsx:109` |
| ☐ | showToast | Could not send message. Please try again. | OK |  | `driver-app/app/driver/lost-and-found-chat.tsx:249` |
| ☐ | showToast | Could not send the photo. Please try again. | OK |  | `driver-app/app/driver/lost-and-found-chat.tsx:315` |
| ☐ | showToast | Could not submit report. Please try again. | OK |  | `driver-app/app/driver/lost-and-found-chat.tsx:190` |
| ☐ | Alert.alert | Could not update status. Please try again. | OK |  | `driver-app/app/driver/lost-and-found-chat.tsx:200` |
| ☐ | showToast | Could not update status. Please try again. | OK |  | `driver-app/app/driver/lost-and-found-chat.tsx:213` |
| ☐ | Alert.alert | Item Not Found | OK |  | `driver-app/app/driver/lost-and-found-chat.tsx:200` |
| ☐ | showToast | Permission Needed | OK |  | `driver-app/app/driver/lost-and-found-chat.tsx:273` |
| ☐ | Alert.alert | Photo Library | OK |  | `driver-app/app/driver/lost-and-found-chat.tsx:261` |
| ☐ | showToast | Photo access is needed to attach an image. | OK |  | `driver-app/app/driver/lost-and-found-chat.tsx:273` |
| ☐ | showToast | Pull to refresh. | OK |  | `driver-app/app/driver/lost-and-found-chat.tsx:109` |
| ☐ | showToast | Send Failed | OK |  | `driver-app/app/driver/lost-and-found-chat.tsx:249` |
| ☐ | Alert.alert | Show the rider what you found | OK |  | `driver-app/app/driver/lost-and-found-chat.tsx:261` |
| ☐ | showToast | Submit Failed | OK |  | `driver-app/app/driver/lost-and-found-chat.tsx:190` |
| ☐ | Alert.alert | Update Failed | OK |  | `driver-app/app/driver/lost-and-found-chat.tsx:200` |
| ☐ | showToast | Update Failed | OK |  | `driver-app/app/driver/lost-and-found-chat.tsx:213` |
| ☐ | showToast | Upload Failed | OK |  | `driver-app/app/driver/lost-and-found-chat.tsx:315` |

### `otp`  (11)

| ✓ | Trigger | Message the user sees | Verdict | Issue | Source |
|---|---|---|---|---|---|
| ☐ | showToast | A new verification code has been sent to your phone. | OK |  | `driver-app/app/otp.tsx:282` |
| ☐ | showToast | A new verification code has been sent to your phone. | OK |  | `driver-app/app/otp.tsx:296` |
| ☐ | showToast | Code Sent | OK |  | `driver-app/app/otp.tsx:282` |
| ☐ | showToast | Code Sent | OK |  | `driver-app/app/otp.tsx:296` |
| ☐ | showToast | Could not resend code. Please try again. | OK |  | `driver-app/app/otp.tsx:298` |
| ☐ | showToast | Could not send a new code. Please try again. | OK |  | `driver-app/app/otp.tsx:284` |
| ☐ | showToast | Invalid Code | OK |  | `driver-app/app/otp.tsx:179` |
| ☐ | showToast | One More Step | OK |  | `driver-app/app/otp.tsx:258` |
| ☐ | showToast | Please agree to our Terms of Service and Privacy Policy to finish creating your account. | OK |  | `driver-app/app/otp.tsx:258` |
| ☐ | showToast | Please enter the ${codeLength}-digit code sent to your phone. | OK |  | `driver-app/app/otp.tsx:179` |
| ☐ | showToast | Verification Failed | OK |  | `driver-app/app/otp.tsx:267` |

### `payout`  (25)

| ✓ | Trigger | Message the user sees | Verdict | Issue | Source |
|---|---|---|---|---|---|
| ☐ | showToast | Check Your Email | OK |  | `driver-app/app/driver/payout.tsx:280` |
| ☐ | showToast | Check Your Email | OK |  | `driver-app/app/driver/payout.tsx:301` |
| ☐ | showToast | Could not save your GST number. Please try again. | OK |  | `driver-app/app/driver/payout.tsx:241` |
| ☐ | showToast | Could not save your SIN. Please try again. | OK |  | `driver-app/app/driver/payout.tsx:264` |
| ☐ | showToast | Could not send your T4A. Please try again. | OK |  | `driver-app/app/driver/payout.tsx:286` |
| ☐ | showToast | Could not send your earnings export. Please try again. | OK |  | `driver-app/app/driver/payout.tsx:303` |
| ☐ | showToast | Could not start verification. Please try again. | OK |  | `driver-app/app/driver/payout.tsx:219` |
| ☐ | showToast | Details received. Stripe is reviewing them; this can take a few minutes. | OK |  | `driver-app/app/driver/payout.tsx:202` |
| ☐ | showToast | Enter your 9-digit Business Number (BN) or full GST number (e.g., 123456789RT0001) | OK |  | `driver-app/app/driver/payout.tsx:229` |
| ☐ | showToast | GST/BN number updated successfully | OK |  | `driver-app/app/driver/payout.tsx:239` |
| ☐ | showToast | Invalid Format | OK |  | `driver-app/app/driver/payout.tsx:229` |
| ☐ | showToast | Invalid Format | OK |  | `driver-app/app/driver/payout.tsx:251` |
| ☐ | showToast | Not finished | OK |  | `driver-app/app/driver/payout.tsx:210` |
| ☐ | showToast | Payouts setup is not available yet. Please try again later. | OK |  | `driver-app/app/driver/payout.tsx:186` |
| ☐ | showToast | Save Failed | OK |  | `driver-app/app/driver/payout.tsx:241` |
| ☐ | showToast | Save Failed | OK |  | `driver-app/app/driver/payout.tsx:264` |
| ☐ | showToast | Send Failed | OK |  | `driver-app/app/driver/payout.tsx:286` |
| ☐ | showToast | Send Failed | OK |  | `driver-app/app/driver/payout.tsx:303` |
| ☐ | showToast | Stripe approved your account — payouts are enabled. | OK |  | `driver-app/app/driver/payout.tsx:202` |
| ☐ | showToast | Stripe onboarding wasn’t completed. You can resume anytime. | OK |  | `driver-app/app/driver/payout.tsx:210` |
| ☐ | showToast | Verification submitted | OK |  | `driver-app/app/driver/payout.tsx:202` |
| ☐ | showToast | Your SIN is 9 digits. | OK |  | `driver-app/app/driver/payout.tsx:251` |
| ☐ | showToast | Your SIN is stored securely for your T4A. | OK |  | `driver-app/app/driver/payout.tsx:261` |
| ☐ | showToast | Your T4A summary for ${latestTaxYear} is on its way. | OK |  | `driver-app/app/driver/payout.tsx:280` |
| ☐ | showToast | Your earnings export for ${year} is on its way. | OK |  | `driver-app/app/driver/payout.tsx:301` |

### `payouts`  (24)

| ✓ | Trigger | Message the user sees | Verdict | Issue | Source |
|---|---|---|---|---|---|
| ☐ | HTTP 409 | A payout is already in progress. Please wait for it to complete. | OK |  | `backend/routes/drivers/payouts.py:965` |
| ☐ | HTTP 409 | A payout is already in progress. Please wait for it to complete. | OK |  | `backend/routes/drivers/payouts.py:1188` |
| ☐ | HTTP 422 | A valid GST/HST Business Number is required before you can be paid. Rideshare drivers must register for GST/HST with the CRA from their first fare. Add your 9-digit Business Number in Payouts to continue. | OK |  | `backend/routes/drivers/payouts.py:773` |
| ☐ | HTTP 422 | Add your SIN before connecting your payout account. It is needed for your T4A tax slip and is passed to Stripe so you are only asked once. | OK |  | `backend/routes/drivers/payouts.py:800` |
| ☐ | HTTP 410 | Cash out has been replaced by automatic weekly payouts — your earnings are sent to your bank every Sunday. | OK |  | `backend/routes/drivers/payouts.py:887` |
| ☐ | HTTP 404 | Driver profile not found | OK |  | `backend/routes/drivers/payouts.py:108` |
| ☐ | HTTP 404 | Driver profile not found | OK |  | `backend/routes/drivers/payouts.py:426` |
| ☐ | HTTP 404 | Driver profile not found | OK |  | `backend/routes/drivers/payouts.py:718` |
| ☐ | HTTP 404 | Driver profile not found | OK |  | `backend/routes/drivers/payouts.py:750` |
| ☐ | HTTP 404 | Driver profile not found | OK |  | `backend/routes/drivers/payouts.py:914` |
| ☐ | HTTP 404 | Driver profile not found | OK |  | `backend/routes/drivers/payouts.py:1115` |
| ☐ | HTTP 404 | Driver profile not found | OK |  | `backend/routes/drivers/payouts.py:1368` |
| ☐ | HTTP 404 | Driver profile not found | OK |  | `backend/routes/drivers/payouts.py:1425` |
| ☐ | HTTP 404 | Driver profile not found | OK |  | `backend/routes/drivers/payouts.py:1472` |
| ☐ | HTTP 404 | Driver/User profile not found | OK |  | `backend/routes/drivers/payouts.py:336` |
| ☐ | HTTP 404 | Driver/User profile not found | OK |  | `backend/routes/drivers/payouts.py:541` |
| ☐ | HTTP 400 | Fee exceeds payout amount | OK |  | `backend/routes/drivers/payouts.py:1132` |
| ☐ | HTTP 400 | Instant payout requires Stripe Connect onboarding. Please complete onboarding from the Payouts screen. | OK |  | `backend/routes/drivers/payouts.py:1148` |
| ☐ | HTTP 403 | Instant payouts are not available in your service area. Your earnings are paid out automatically every Sunday. | OK |  | `backend/routes/drivers/payouts.py:840` |
| ☐ | HTTP 400 | Insufficient funds | OK |  | `backend/routes/drivers/payouts.py:923` |
| ☐ | HTTP 400 | Insufficient funds | OK |  | `backend/routes/drivers/payouts.py:1136` |
| ☐ | HTTP 410 | Manual payouts disabled | OK |  | `backend/routes/drivers/payouts.py:909` |
| ☐ | HTTP 400 | No bank account linked | OK |  | `backend/routes/drivers/payouts.py:931` |
| ☐ | HTTP 422 | Your SIN must be on file before you can be paid. Add it in Payouts — it is needed for your T4A tax slip and is stored encrypted. | OK |  | `backend/routes/drivers/payouts.py:819` |

### `profile`  (43)

| ✓ | Trigger | Message the user sees | Verdict | Issue | Source |
|---|---|---|---|---|---|
| ☐ | HTTP 409 | A driver account with this phone already exists. Log in to that account instead. | OK |  | `backend/routes/drivers/profile.py:772` |
| ☐ | Alert.alert | Are you sure you want to sign out? | OK |  | `driver-app/app/driver/(tabs)/profile.tsx:293` |
| ☐ | showToast | Camera access is needed. | OK |  | `driver-app/app/driver/(tabs)/profile.tsx:193` |
| ☐ | Alert.alert | Choose how to update your profile photo. | OK |  | `driver-app/app/driver/(tabs)/profile.tsx:184` |
| ☐ | showToast | Copied! | OK |  | `driver-app/app/driver/(tabs)/profile.tsx:89` |
| ☐ | showToast | Could not upload your photo. Please try again. | OK |  | `driver-app/app/driver/(tabs)/profile.tsx:215` |
| ☐ | HTTP 404 | Driver not found | OK |  | `backend/routes/drivers/profile.py:86` |
| ☐ | HTTP 404 | Driver not found | OK |  | `backend/routes/drivers/profile.py:903` |
| ☐ | HTTP 404 | Driver not found | OK |  | `backend/routes/drivers/profile.py:928` |
| ☐ | HTTP 404 | Driver not found | OK |  | `backend/routes/drivers/profile.py:949` |
| ☐ | showToast | Failed to save your licence info. Please try again. | OK |  | `driver-app/app/driver/(tabs)/profile.tsx:286` |
| ☐ | showToast | Failed to update your profile. Please try again. | OK |  | `driver-app/app/driver/(tabs)/profile.tsx:249` |
| ☐ | showToast | Library access is needed. | OK |  | `driver-app/app/driver/(tabs)/profile.tsx:202` |
| ☐ | showToast | Licence Info Saved | OK |  | `driver-app/app/driver/(tabs)/profile.tsx:284` |
| ☐ | showToast | Missing Info | OK |  | `driver-app/app/driver/(tabs)/profile.tsx:272` |
| ☐ | showToast | Permission Denied | OK |  | `driver-app/app/driver/(tabs)/profile.tsx:193` |
| ☐ | showToast | Permission Denied | OK |  | `driver-app/app/driver/(tabs)/profile.tsx:202` |
| ☐ | showToast | Photo Updated | OK |  | `driver-app/app/driver/(tabs)/profile.tsx:213` |
| ☐ | showToast | Profile Updated | OK |  | `driver-app/app/driver/(tabs)/profile.tsx:245` |
| ☐ | showToast | Referral code copied to clipboard | OK |  | `driver-app/app/driver/(tabs)/profile.tsx:89` |
| ☐ | Alert.alert | Sign Out | OK |  | `driver-app/app/driver/(tabs)/profile.tsx:293` |
| ☐ | Alert.alert | Sign Out Failed | OK |  | `driver-app/app/driver/(tabs)/profile.tsx:293` |
| ☐ | Alert.alert | Sign Out Failed | OK |  | `driver-app/app/driver/(tabs)/profile.tsx:310` |
| ☐ | showToast | Sign Out Failed | OK |  | `driver-app/app/driver/(tabs)/profile.tsx:300` |
| ☐ | showToast | Sign Out Failed | OK |  | `driver-app/app/driver/(tabs)/profile.tsx:323` |
| ☐ | Alert.alert | Sign out everywhere | OK |  | `driver-app/app/driver/(tabs)/profile.tsx:310` |
| ☐ | Alert.alert | Sign out of all devices? | OK |  | `driver-app/app/driver/(tabs)/profile.tsx:310` |
| ☐ | Alert.alert | Take Photo | OK |  | `driver-app/app/driver/(tabs)/profile.tsx:184` |
| ☐ | showToast | Thanks — your licence details have been updated. | OK |  | `driver-app/app/driver/(tabs)/profile.tsx:284` |
| ☐ | showToast | Update Failed | OK |  | `driver-app/app/driver/(tabs)/profile.tsx:249` |
| ☐ | showToast | Update Failed | OK |  | `driver-app/app/driver/(tabs)/profile.tsx:286` |
| ☐ | Alert.alert | Update Photo | OK |  | `driver-app/app/driver/(tabs)/profile.tsx:184` |
| ☐ | showToast | Upload Failed | OK |  | `driver-app/app/driver/(tabs)/profile.tsx:215` |
| ☐ | Alert.alert | You will be signed out everywhere this driver account is logged in. Use this if your phone was lost or you suspect someone else has access. | OK |  | `driver-app/app/driver/(tabs)/profile.tsx:310` |
| ☐ | HTTP 403 | Your SIN is already on file and cannot be changed from the app. Contact support to request a correction — an admin will verify and update it. | OK |  | `backend/routes/drivers/profile.py:193` |
| ☐ | HTTP 409 | Your SIN was already saved by another request and cannot be overwritten. Contact support if it needs a correction. | OK |  | `backend/routes/drivers/profile.py:285` |
| ☐ | showToast | Your information has been saved. | OK |  | `driver-app/app/driver/(tabs)/profile.tsx:245` |
| ☐ | showToast | Your profile photo has been submitted for review. | OK |  | `driver-app/app/driver/(tabs)/profile.tsx:213` |
| ☐ | Alert.alert | Your session could not be closed. Please try again. | OK |  | `driver-app/app/driver/(tabs)/profile.tsx:293` |
| ☐ | Alert.alert | Your session could not be closed. Please try again. | OK |  | `driver-app/app/driver/(tabs)/profile.tsx:310` |
| ☐ | showToast | Your session could not be closed. Please try again. | OK |  | `driver-app/app/driver/(tabs)/profile.tsx:300` |
| ☐ | showToast | Your session could not be closed. Please try again. | OK |  | `driver-app/app/driver/(tabs)/profile.tsx:323` |
| ☐ | HTTP 422 | str(exc) | OK |  | `backend/routes/drivers/profile.py:208` |

### `profile-setup`  (7)

| ✓ | Trigger | Message the user sees | Verdict | Issue | Source |
|---|---|---|---|---|---|
| ☐ | Alert.alert | Change phone number? | OK |  | `driver-app/app/profile-setup.tsx:180` |
| ☐ | showToast | Referral applied | OK |  | `driver-app/app/profile-setup.tsx:273` |
| ☐ | showToast | Referral code | OK |  | `driver-app/app/profile-setup.tsx:266` |
| ☐ | Alert.alert | Sign Out | OK |  | `driver-app/app/profile-setup.tsx:180` |
| ☐ | showToast | That referral code couldn't be applied. | OK |  | `driver-app/app/profile-setup.tsx:266` |
| ☐ | Alert.alert | This will sign you out and return to the login screen. Any progress here will be lost. | OK |  | `driver-app/app/profile-setup.tsx:180` |
| ☐ | showToast | Your referral code was added. | OK |  | `driver-app/app/profile-setup.tsx:273` |

### `quests`  (6)

| ✓ | Trigger | Message the user sees | Verdict | Issue | Source |
|---|---|---|---|---|---|
| ☐ | Alert.alert | ${money(r?.reward_amount ?? reward)} added to your wallet. | OK |  | `driver-app/app/driver/quests.tsx:110` |
| ☐ | Alert.alert | Could not claim | OK |  | `driver-app/app/driver/quests.tsx:112` |
| ☐ | Alert.alert | Could not claim your reward. Please try again. | OK |  | `driver-app/app/driver/quests.tsx:112` |
| ☐ | Alert.alert | Could not join | OK |  | `driver-app/app/driver/quests.tsx:103` |
| ☐ | Alert.alert | Could not join the quest. Please try again. | OK |  | `driver-app/app/driver/quests.tsx:103` |
| ☐ | Alert.alert | Reward claimed! 🎉 | OK |  | `driver-app/app/driver/quests.tsx:110` |

### `reactivate-account`  (8)

| ✓ | Trigger | Message the user sees | Verdict | Issue | Source |
|---|---|---|---|---|---|
| ☐ | showToast | Account Deleted | OK |  | `driver-app/app/reactivate-account.tsx:72` |
| ☐ | showToast | Could not reactivate. Please try again. | OK |  | `driver-app/app/reactivate-account.tsx:75` |
| ☐ | showToast | Missing reactivation token. Please sign in again. | OK |  | `driver-app/app/reactivate-account.tsx:50` |
| ☐ | showToast | Reactivation Failed | OK |  | `driver-app/app/reactivate-account.tsx:50` |
| ☐ | showToast | Reactivation Failed | OK |  | `driver-app/app/reactivate-account.tsx:75` |
| ☐ | showToast | This account has been permanently deleted. Please create a new one. | OK |  | `driver-app/app/reactivate-account.tsx:72` |
| ☐ | showToast | Welcome back | OK |  | `driver-app/app/reactivate-account.tsx:67` |
| ☐ | showToast | Your account has been reactivated. | OK |  | `driver-app/app/reactivate-account.tsx:67` |

### `referral`  (4)

| ✓ | Trigger | Message the user sees | Verdict | Issue | Source |
|---|---|---|---|---|---|
| ☐ | showToast | Copied! | OK |  | `driver-app/app/driver/referral.tsx:102` |
| ☐ | showToast | Copied! | OK |  | `driver-app/app/driver/referral.tsx:115` |
| ☐ | showToast | Referral code copied to clipboard | OK |  | `driver-app/app/driver/referral.tsx:102` |
| ☐ | showToast | Referral code copied to clipboard | OK |  | `driver-app/app/driver/referral.tsx:115` |

### `referrals`  (6)

| ✓ | Trigger | Message the user sees | Verdict | Issue | Source |
|---|---|---|---|---|---|
| ☐ | HTTP 404 | Driver not found | OK |  | `backend/routes/drivers/referrals.py:97` |
| ☐ | HTTP 404 | Driver not found | OK |  | `backend/routes/drivers/referrals.py:285` |
| ☐ | HTTP 404 | Driver not found | OK |  | `backend/routes/drivers/referrals.py:360` |
| ☐ | HTTP 404 | Invalid referral code | OK |  | `backend/routes/drivers/referrals.py:252` |
| ☐ | HTTP 400 | Referral code already applied | OK |  | `backend/routes/drivers/referrals.py:215` |
| ☐ | HTTP 400 | You can't use your own referral code | OK |  | `backend/routes/drivers/referrals.py:257` |

### `register`  (10)

| ✓ | Trigger | Message the user sees | Verdict | Issue | Source |
|---|---|---|---|---|---|
| ☐ | AlertDialog | 911 dial failed: | OK |  | `driver-app/lib/androidAuto/register.ts:404` |
| ☐ | AlertDialog | 911 dial failed: | OK |  | `driver-app/lib/androidAuto/register.ts:434` |
| ☐ | AlertDialog | Alerts Spinr safety and your emergency contacts with your location. | OK |  | `driver-app/lib/androidAuto/register.ts:468` |
| ☐ | AlertDialog | Call 911 | OK |  | `driver-app/lib/androidAuto/register.ts:404` |
| ☐ | AlertDialog | Call 911 | OK |  | `driver-app/lib/androidAuto/register.ts:434` |
| ☐ | AlertDialog | Emergency alert FAILED to send | OK |  | `driver-app/lib/androidAuto/register.ts:434` |
| ☐ | AlertDialog | Emergency alert sent | OK |  | `driver-app/lib/androidAuto/register.ts:404` |
| ☐ | AlertDialog | Send alert | OK |  | `driver-app/lib/androidAuto/register.ts:468` |
| ☐ | AlertDialog | Send emergency alert? | OK |  | `driver-app/lib/androidAuto/register.ts:468` |
| ☐ | AlertDialog | Use your phone, or call 911 directly. | OK |  | `driver-app/lib/androidAuto/register.ts:434` |

### `report-safety`  (12)

| ✓ | Trigger | Message the user sees | Verdict | Issue | Source |
|---|---|---|---|---|---|
| ☐ | Alert.alert | Add Evidence | OK |  | `driver-app/app/report-safety.tsx:63` |
| ☐ | showToast | Camera access is needed. | OK |  | `driver-app/app/report-safety.tsx:72` |
| ☐ | Alert.alert | Choose a source | OK |  | `driver-app/app/report-safety.tsx:63` |
| ☐ | showToast | Could not submit your report. Please try again. | OK |  | `driver-app/app/report-safety.tsx:150` |
| ☐ | showToast | Library access is needed. | OK |  | `driver-app/app/report-safety.tsx:79` |
| ☐ | showToast | Permission Denied | OK |  | `driver-app/app/report-safety.tsx:72` |
| ☐ | showToast | Permission Denied | OK |  | `driver-app/app/report-safety.tsx:79` |
| ☐ | Alert.alert | Photo Library | OK |  | `driver-app/app/report-safety.tsx:63` |
| ☐ | showToast | Report Sent — Photos Failed | OK |  | `driver-app/app/report-safety.tsx:140` |
| ☐ | showToast | Report Submitted | OK |  | `driver-app/app/report-safety.tsx:146` |
| ☐ | showToast | Your report was submitted, but ${failedPhotos} of ${photos.length} photo${photos.length === 1 ? '' : 's'} could not be attached. Our team may contact you for them. | OK |  | `driver-app/app/report-safety.tsx:140` |
| ☐ | showToast | Your safety report has been submitted. Our trust and safety team will review it promptly. | OK |  | `driver-app/app/report-safety.tsx:146` |

### `ride_cancel`  (12)

| ✓ | Trigger | Message the user sees | Verdict | Issue | Source |
|---|---|---|---|---|---|
| ☐ | HTTP 400 | Arrival time not recorded | OK |  | `backend/routes/drivers/ride_cancel.py:467` |
| ☐ | HTTP 404 | Driver not found | OK |  | `backend/routes/drivers/ride_cancel.py:62` |
| ☐ | HTTP 404 | Driver not found | OK |  | `backend/routes/drivers/ride_cancel.py:455` |
| ☐ | HTTP 404 | Driver not found | OK |  | `backend/routes/drivers/ride_cancel.py:746` |
| ☐ | HTTP 400 | Must wait {remaining} more seconds before marking no-show | OK |  | `backend/routes/drivers/ride_cancel.py:497` |
| ☐ | HTTP 403 | Not your assigned ride | OK |  | `backend/routes/drivers/ride_cancel.py:77` |
| ☐ | HTTP 403 | Not your assigned ride | OK |  | `backend/routes/drivers/ride_cancel.py:461` |
| ☐ | HTTP 409 | Ride can no longer be cancelled (it has started or already ended) | OK |  | `backend/routes/drivers/ride_cancel.py:138` |
| ☐ | HTTP 409 | Ride can no longer be marked no-show (it has started or already ended) | OK |  | `backend/routes/drivers/ride_cancel.py:516` |
| ☐ | HTTP 404 | Ride not found | OK |  | `backend/routes/drivers/ride_cancel.py:66` |
| ☐ | HTTP 404 | Ride not found | OK |  | `backend/routes/drivers/ride_cancel.py:459` |
| ☐ | HTTP 404 | Ride not found | OK |  | `backend/routes/drivers/ride_cancel.py:756` |

### `ride_complete`  (3)

| ✓ | Trigger | Message the user sees | Verdict | Issue | Source |
|---|---|---|---|---|---|
| ☐ | HTTP 404 | Driver not found | OK |  | `backend/routes/drivers/ride_complete.py:371` |
| ☐ | HTTP 404 | Ride not found | OK |  | `backend/routes/drivers/ride_complete.py:377` |
| ☐ | HTTP 409 | {'code': 'completion_confirmation_required', 'distance_band': distance_band, 'distance_meters': distance_meters} | OK |  | `backend/routes/drivers/ride_complete.py:295` |

### `ride_flow`  (20)

| ✓ | Trigger | Message the user sees | Verdict | Issue | Source |
|---|---|---|---|---|---|
| ☐ | HTTP 403 | Cannot accept your own ride | OK |  | `backend/routes/drivers/ride_flow.py:152` |
| ☐ | HTTP 404 | Driver not found | OK |  | `backend/routes/drivers/ride_flow.py:95` |
| ☐ | HTTP 404 | Driver not found | OK |  | `backend/routes/drivers/ride_flow.py:624` |
| ☐ | HTTP 404 | Driver not found | OK |  | `backend/routes/drivers/ride_flow.py:919` |
| ☐ | HTTP 404 | Driver not found | OK |  | `backend/routes/drivers/ride_flow.py:1048` |
| ☐ | HTTP 404 | Driver not found | OK |  | `backend/routes/drivers/ride_flow.py:1152` |
| ☐ | HTTP 400 | Invalid OTP | OK |  | `backend/routes/drivers/ride_flow.py:1086` |
| ☐ | HTTP 403 | No active offer for this ride | OK |  | `backend/routes/drivers/ride_flow.py:344` |
| ☐ | HTTP 403 | Not authorized to decline this ride | OK |  | `backend/routes/drivers/ride_flow.py:670` |
| ☐ | HTTP 400 | Ride not assigned to you | OK |  | `backend/routes/drivers/ride_flow.py:351` |
| ☐ | HTTP 404 | Ride not found | OK |  | `backend/routes/drivers/ride_flow.py:147` |
| ☐ | HTTP 404 | Ride not found | OK |  | `backend/routes/drivers/ride_flow.py:628` |
| ☐ | HTTP 404 | Ride not found | OK |  | `backend/routes/drivers/ride_flow.py:925` |
| ☐ | HTTP 404 | Ride not found | OK |  | `backend/routes/drivers/ride_flow.py:1054` |
| ☐ | HTTP 404 | Ride not found | OK |  | `backend/routes/drivers/ride_flow.py:1158` |
| ☐ | HTTP 409 | This ride has no pickup code — contact support to start it | OK |  | `backend/routes/drivers/ride_flow.py:1073` |
| ☐ | HTTP 410 | Use POST /rides/{ride_id}/verify-otp to start a ride in production. | OK |  | `backend/routes/drivers/ride_flow.py:1144` |
| ☐ | HTTP 400 | You are {distance_m}m away from the pickup. Please move within 200m of the pickup location to mark arrival. | OK |  | `backend/routes/drivers/ride_flow.py:945` |
| ☐ | HTTP 409 | detail | FLAG | bare machine token, not a sentence | `backend/routes/drivers/ride_flow.py:68` |
| ☐ | HTTP 409 | f"This ride can no longer be declined — it's {_decline_phrase}." if _decline_phrase else 'This ride can no longer be declined.' | OK |  | `backend/routes/drivers/ride_flow.py:631` |

### `ride_reads`  (9)

| ✓ | Trigger | Message the user sees | Verdict | Issue | Source |
|---|---|---|---|---|---|
| ☐ | HTTP 404 | Driver not found | OK |  | `backend/routes/drivers/ride_reads.py:64` |
| ☐ | HTTP 404 | Driver not found | OK |  | `backend/routes/drivers/ride_reads.py:267` |
| ☐ | HTTP 404 | Driver not found | OK |  | `backend/routes/drivers/ride_reads.py:459` |
| ☐ | HTTP 410 | Offer no longer available | OK |  | `backend/routes/drivers/ride_reads.py:306` |
| ☐ | HTTP 410 | Offer no longer available | OK |  | `backend/routes/drivers/ride_reads.py:335` |
| ☐ | HTTP 410 | Offer no longer available | OK |  | `backend/routes/drivers/ride_reads.py:341` |
| ☐ | HTTP 404 | Offer not found | OK |  | `backend/routes/drivers/ride_reads.py:271` |
| ☐ | HTTP 404 | Offer not found | OK |  | `backend/routes/drivers/ride_reads.py:307` |
| ☐ | HTTP 404 | Offer not found | OK |  | `backend/routes/drivers/ride_reads.py:326` |

### `status`  (5)

| ✓ | Trigger | Message the user sees | Verdict | Issue | Source |
|---|---|---|---|---|---|
| ☐ | HTTP 409 | Cannot go offline during an active trip. Please complete the current ride first. | OK |  | `backend/routes/drivers/status.py:233` |
| ☐ | HTTP 404 | Driver not found | OK |  | `backend/routes/drivers/status.py:45` |
| ☐ | HTTP 404 | Driver not found | OK |  | `backend/routes/drivers/status.py:146` |
| ☐ | HTTP 403 | Not authorized | OK |  | `backend/routes/drivers/status.py:72` |
| ☐ | HTTP 403 | Not authorized | OK |  | `backend/routes/drivers/status.py:149` |

### `stripe-onboarding`  (2)

| ✓ | Trigger | Message the user sees | Verdict | Issue | Source |
|---|---|---|---|---|---|
| ☐ | showToast | Verification Complete | OK |  | `driver-app/app/driver/stripe-onboarding.tsx:147` |
| ☐ | showToast | Verification Failed | OK |  | `driver-app/app/driver/stripe-onboarding.tsx:147` |

### `subscription`  (23)

| ✓ | Trigger | Message the user sees | Verdict | Issue | Source |
|---|---|---|---|---|---|
| ☐ | Alert.alert | Are you sure? You can still drive until your current plan expires. | OK |  | `driver-app/app/driver/subscription.tsx:198` |
| ☐ | Alert.alert | Cancel Plan | OK |  | `driver-app/app/driver/subscription.tsx:198` |
| ☐ | Alert.alert | Cancel Subscription | OK |  | `driver-app/app/driver/subscription.tsx:198` |
| ☐ | Alert.alert | Could not cancel your subscription. Please try again. | OK |  | `driver-app/app/driver/subscription.tsx:198` |
| ☐ | showToast | Could not cancel your subscription. Please try again. | OK |  | `driver-app/app/driver/subscription.tsx:211` |
| ☐ | showToast | Could not send the invoice. Please try again. | OK |  | `driver-app/app/driver/subscription.tsx:227` |
| ☐ | showToast | Could not subscribe. Please try again. | OK |  | `driver-app/app/driver/subscription.tsx:193` |
| ☐ | showToast | Invoice Sent | OK |  | `driver-app/app/driver/subscription.tsx:225` |
| ☐ | showToast | Invoice emailed to your address on file. | OK |  | `driver-app/app/driver/subscription.tsx:225` |
| ☐ | Alert.alert | Keep Plan | OK |  | `driver-app/app/driver/subscription.tsx:198` |
| ☐ | showToast | Processing... | OK |  | `driver-app/app/driver/subscription.tsx:178` |
| ☐ | showToast | Processing... | OK |  | `driver-app/app/driver/subscription.tsx:181` |
| ☐ | Alert.alert | Subscribe to "${plan.name}" for $${plan.price.toFixed(2)}/${getDurationLabel(plan.duration_days).toLowerCase()}?${quotaDisclaimer(plan)} | OK |  | `driver-app/app/driver/subscription.tsx:134` |
| ☐ | showToast | Subscribed! | OK |  | `driver-app/app/driver/subscription.tsx:176` |
| ☐ | showToast | Subscribed! | OK |  | `driver-app/app/driver/subscription.tsx:189` |
| ☐ | Alert.alert | Switch Plan? | OK |  | `driver-app/app/driver/subscription.tsx:125` |
| ☐ | Alert.alert | You currently have "${currentSub.subscription.plan_name}". Switch to "${plan.name}" for $${plan.price.toFixed(2)}?${quotaDisclaimer(plan)} | OK |  | `driver-app/app/driver/subscription.tsx:125` |
| ☐ | showToast | You're now on the ${plan.name} plan. Go online and start earning! | OK |  | `driver-app/app/driver/subscription.tsx:176` |
| ☐ | showToast | You're now on the ${plan.name} plan. Go online and start earning! | OK |  | `driver-app/app/driver/subscription.tsx:189` |
| ☐ | showToast | Your payment is being confirmed. This may take a moment. | OK |  | `driver-app/app/driver/subscription.tsx:178` |
| ☐ | showToast | Your payment is being confirmed. This may take a moment. | OK |  | `driver-app/app/driver/subscription.tsx:181` |
| ☐ | Alert.alert | Your subscription has been cancelled. | OK |  | `driver-app/app/driver/subscription.tsx:198` |
| ☐ | showToast | Your subscription has been cancelled. | OK |  | `driver-app/app/driver/subscription.tsx:208` |

### `subscriptions`  (15)

| ✓ | Trigger | Message the user sees | Verdict | Issue | Source |
|---|---|---|---|---|---|
| ☐ | HTTP 409 | A checkout is already in progress. Please finish or cancel it first. | OK |  | `backend/routes/drivers/subscriptions.py:713` |
| ☐ | HTTP 404 | Driver profile not found | OK |  | `backend/routes/drivers/subscriptions.py:342` |
| ☐ | HTTP 404 | Driver profile not found | OK |  | `backend/routes/drivers/subscriptions.py:1420` |
| ☐ | HTTP 404 | Driver profile not found | OK |  | `backend/routes/drivers/subscriptions.py:1497` |
| ☐ | HTTP 404 | Driver profile not found | OK |  | `backend/routes/drivers/subscriptions.py:1564` |
| ☐ | HTTP 400 | No active subscription | OK |  | `backend/routes/drivers/subscriptions.py:1577` |
| ☐ | HTTP 404 | Payment not found | OK |  | `backend/routes/drivers/subscriptions.py:1501` |
| ☐ | HTTP 404 | Plan not found or inactive | OK |  | `backend/routes/drivers/subscriptions.py:359` |
| ☐ | HTTP 403 | Spinr Pass is not available in your service area | OK |  | `backend/routes/drivers/subscriptions.py:350` |
| ☐ | HTTP 404 | Subscription session not found | OK |  | `backend/routes/drivers/subscriptions.py:796` |
| ☐ | HTTP 404 | Subscription session not found | OK |  | `backend/routes/drivers/subscriptions.py:805` |
| ☐ | HTTP 422 | This plan is not available for your service area. Please choose a plan that covers your area. | OK |  | `backend/routes/drivers/subscriptions.py:386` |
| ☐ | HTTP 409 | This subscription plan is misconfigured. Please contact support. | OK |  | `backend/routes/drivers/subscriptions.py:509` |
| ☐ | HTTP 409 | This subscription plan is misconfigured. Please contact support. | OK |  | `backend/routes/drivers/subscriptions.py:535` |
| ☐ | HTTP 422 | Your vehicle type is not compatible with this plan. Please choose a plan that supports your vehicle. | OK |  | `backend/routes/drivers/subscriptions.py:368` |

### `success`  (6)

| ✓ | Trigger | Message the user sees | Verdict | Issue | Source |
|---|---|---|---|---|---|
| ☐ | showToast | Payment Successful! | OK |  | `driver-app/app/subscription/success.tsx:63` |
| ☐ | showToast | Processing... | OK |  | `driver-app/app/subscription/success.tsx:65` |
| ☐ | showToast | Processing... | OK |  | `driver-app/app/subscription/success.tsx:72` |
| ☐ | showToast | Your Spinr Pass is now active. Go online and start earning! | OK |  | `driver-app/app/subscription/success.tsx:63` |
| ☐ | showToast | Your payment is being confirmed. This may take a moment. | OK |  | `driver-app/app/subscription/success.tsx:65` |
| ☐ | showToast | Your payment is being confirmed. This may take a moment. | OK |  | `driver-app/app/subscription/success.tsx:72` |

### `tax-documents`  (6)

| ✓ | Trigger | Message the user sees | Verdict | Issue | Source |
|---|---|---|---|---|---|
| ☐ | showToast | Check Your Email | OK |  | `driver-app/app/driver/tax-documents.tsx:99` |
| ☐ | showToast | Could not load tax documents. Please try again. | OK |  | `driver-app/app/driver/tax-documents.tsx:74` |
| ☐ | showToast | Could not send the document. Please try again. | OK |  | `driver-app/app/driver/tax-documents.tsx:105` |
| ☐ | showToast | Load Failed | OK |  | `driver-app/app/driver/tax-documents.tsx:74` |
| ☐ | showToast | Send Failed | OK |  | `driver-app/app/driver/tax-documents.tsx:105` |
| ☐ | showToast | Your T4A summary for ${doc.tax_year} is on its way. | OK |  | `driver-app/app/driver/tax-documents.tsx:99` |

### `tax_exports`  (8)

| ✓ | Trigger | Message the user sees | Verdict | Issue | Source |
|---|---|---|---|---|---|
| ☐ | HTTP 422 | Choose either a weekly or a monthly statement. | OK |  | `backend/routes/drivers/tax_exports.py:479` |
| ☐ | HTTP 404 | Driver profile not found | OK |  | `backend/routes/drivers/tax_exports.py:71` |
| ☐ | HTTP 404 | Driver profile not found | OK |  | `backend/routes/drivers/tax_exports.py:147` |
| ☐ | HTTP 404 | Driver profile not found | OK |  | `backend/routes/drivers/tax_exports.py:489` |
| ☐ | HTTP 400 | No email address on file to send the document to. | OK |  | `backend/routes/drivers/tax_exports.py:275` |
| ☐ | HTTP 400 | No email address on file to send the export to. | OK |  | `backend/routes/drivers/tax_exports.py:847` |
| ☐ | HTTP 422 | We couldn't read that start date. Please choose it again. | OK |  | `backend/routes/drivers/tax_exports.py:483` |
| ☐ | HTTP 422 | str(e) | OK |  | `backend/routes/drivers/tax_exports.py:497` |

### `useDriverDashboard`  (40)

| ✓ | Trigger | Message the user sees | Verdict | Issue | Source |
|---|---|---|---|---|---|
| ☐ | showToast | ${riderName} tipped you $${amount.toFixed(2)}. | OK |  | `driver-app/hooks/useDriverDashboard.ts:1188` |
| ☐ | AlertDialog | A document is expiring — tap to update it | OK |  | `driver-app/hooks/useDriverDashboard.ts:2223` |
| ☐ | AlertDialog | Account Disabled | OK |  | `driver-app/hooks/useDriverDashboard.ts:1856` |
| ☐ | showToast | Account disabled | OK |  | `driver-app/hooks/useDriverDashboard.ts:1855` |
| ☐ | showToast | Activate your Spinr Pass to go online. | OK |  | `driver-app/hooks/useDriverDashboard.ts:1833` |
| ☐ | showToast | Another driver accepted this ride. | OK |  | `driver-app/hooks/useDriverDashboard.ts:1101` |
| ☐ | showToast | Background location needed | OK |  | `driver-app/hooks/useDriverDashboard.ts:1885` |
| ☐ | showToast | Cannot Go Online | OK |  | `driver-app/hooks/useDriverDashboard.ts:1865` |
| ☐ | showToast | Daily ride limit reached | OK |  | `driver-app/hooks/useDriverDashboard.ts:1844` |
| ☐ | AlertDialog | Document Expiring | OK |  | `driver-app/hooks/useDriverDashboard.ts:2223` |
| ☐ | showToast | Enable 'Allow all the time' in Settings to go online and receive ride offers. | OK |  | `driver-app/hooks/useDriverDashboard.ts:1885` |
| ☐ | showToast | Failed to update status. Please try again. | OK |  | `driver-app/hooks/useDriverDashboard.ts:1865` |
| ☐ | showToast | Internet offline | OK |  | `driver-app/hooks/useDriverDashboard.ts:1758` |
| ☐ | showToast | Looking for the next ride... | OK |  | `driver-app/hooks/useDriverDashboard.ts:1078` |
| ☐ | showToast | New notification | OK |  | `driver-app/hooks/useDriverDashboard.ts:2241` |
| ☐ | showToast | No driver was available — the ride was automatically cancelled. | OK |  | `driver-app/hooks/useDriverDashboard.ts:1115` |
| ☐ | showToast | Offer expired | OK |  | `driver-app/hooks/useDriverDashboard.ts:1078` |
| ☐ | AlertDialog | Renew Now | OK |  | `driver-app/hooks/useDriverDashboard.ts:2214` |
| ☐ | showToast | Ride Cancelled | OK |  | `driver-app/hooks/useDriverDashboard.ts:1115` |
| ☐ | showToast | Ride Cancelled | OK |  | `driver-app/hooks/useDriverDashboard.ts:2211` |
| ☐ | showToast | Ride taken | OK |  | `driver-app/hooks/useDriverDashboard.ts:1101` |
| ☐ | showToast | Something went wrong toggling your status. Please try again. | OK |  | `driver-app/hooks/useDriverDashboard.ts:1905` |
| ☐ | AlertDialog | Spinr Pass Expiring | OK |  | `driver-app/hooks/useDriverDashboard.ts:2214` |
| ☐ | AlertDialog | Spinr Pass Required | OK |  | `driver-app/hooks/useDriverDashboard.ts:1834` |
| ☐ | showToast | Spinr Pass required | OK |  | `driver-app/hooks/useDriverDashboard.ts:1833` |
| ☐ | showToast | Tap to view details in your notifications list. | OK |  | `driver-app/hooks/useDriverDashboard.ts:2241` |
| ☐ | showToast | The rider has cancelled this ride. | OK |  | `driver-app/hooks/useDriverDashboard.ts:1115` |
| ☐ | showToast | The rider has cancelled this ride. | OK |  | `driver-app/hooks/useDriverDashboard.ts:2211` |
| ☐ | AlertDialog | Update Now | OK |  | `driver-app/hooks/useDriverDashboard.ts:2223` |
| ☐ | showToast | You got a tip! | OK |  | `driver-app/hooks/useDriverDashboard.ts:1188` |
| ☐ | AlertDialog | You need an active subscription to go online. | OK |  | `driver-app/hooks/useDriverDashboard.ts:1834` |
| ☐ | AlertDialog | You were taken offline because you missed several ride offers in a row. | OK |  | `driver-app/hooks/useDriverDashboard.ts:2206` |
| ☐ | AlertDialog | You were taken offline because you missed several ride offers in a row. Tap "Go Online" when you\'re ready. | OK |  | `driver-app/hooks/useDriverDashboard.ts:1109` |
| ☐ | showToast | You've used today's Spinr Pass rides. They reset at midnight. | OK |  | `driver-app/hooks/useDriverDashboard.ts:1844` |
| ☐ | AlertDialog | You\'re now offline | OK |  | `driver-app/hooks/useDriverDashboard.ts:1109` |
| ☐ | AlertDialog | You\'re now offline | OK |  | `driver-app/hooks/useDriverDashboard.ts:2206` |
| ☐ | AlertDialog | Your Spinr Pass expires soon — renew to keep earning | OK |  | `driver-app/hooks/useDriverDashboard.ts:2214` |
| ☐ | AlertDialog | Your account has been suspended or banned. You can submit an appeal for review. | OK |  | `driver-app/hooks/useDriverDashboard.ts:1856` |
| ☐ | showToast | Your account is currently suspended or banned. | OK |  | `driver-app/hooks/useDriverDashboard.ts:1855` |
| ☐ | showToast | Your internet is offline. Reconnect and try again. | OK |  | `driver-app/hooks/useDriverDashboard.ts:1758` |

### `vehicle-info`  (18)

| ✓ | Trigger | Message the user sees | Verdict | Issue | Source |
|---|---|---|---|---|---|
| ☐ | Alert.alert | );                             }                             await updateDriverMe.mutateAsync({                                 ...form,                                 vehicle_year: getVehicleYearValue(form.vehicle_year | OK |  | `driver-app/app/vehicle-info.tsx:205` |
| ☐ | Alert.alert | Changing your vehicle information will require admin re-verification. You will obtain a 'Pending' status and cannot go online until approved. Continue? | OK |  | `driver-app/app/vehicle-info.tsx:205` |
| ☐ | showToast | Could not load vehicle types. Check your connection. | OK |  | `driver-app/app/vehicle-info.tsx:133` |
| ☐ | Alert.alert | Could not update your vehicle info. Please try again. | OK |  | `driver-app/app/vehicle-info.tsx:205` |
| ☐ | showToast | Could not update your vehicle info. Please try again. | OK |  | `driver-app/app/vehicle-info.tsx:236` |
| ☐ | showToast | Missing Information | OK |  | `driver-app/app/vehicle-info.tsx:202` |
| ☐ | showToast | Please fill in all required fields marked with * | OK |  | `driver-app/app/vehicle-info.tsx:202` |
| ☐ | Alert.alert | Update & Verify | OK |  | `driver-app/app/vehicle-info.tsx:205` |
| ☐ | Alert.alert | Update Failed | OK |  | `driver-app/app/vehicle-info.tsx:205` |
| ☐ | showToast | Update Failed | OK |  | `driver-app/app/vehicle-info.tsx:236` |
| ☐ | Alert.alert | Update Vehicle Info | OK |  | `driver-app/app/vehicle-info.tsx:205` |
| ☐ | Alert.alert | Vehicle Saved | OK |  | `driver-app/app/vehicle-info.tsx:205` |
| ☐ | showToast | Vehicle Saved | OK |  | `driver-app/app/vehicle-info.tsx:233` |
| ☐ | Alert.alert | Vehicle information updated. Please wait for admin approval. | OK |  | `driver-app/app/vehicle-info.tsx:205` |
| ☐ | showToast | Vehicle information updated. Please wait for admin approval. | OK |  | `driver-app/app/vehicle-info.tsx:233` |
| ☐ | Alert.alert | ]                             // and [ | OK |  | `driver-app/app/vehicle-info.tsx:205` |
| ☐ | Alert.alert | ] automatically. We still call                             // the legacy store fetchers so screens that haven | OK |  | `driver-app/app/vehicle-info.tsx:205` |
| ☐ | Alert.alert | s a brief window                             // where tokens are set in store but not yet in the API client.                             // This retry ensures we have the token before making the call.                     | OK |  | `driver-app/app/vehicle-info.tsx:205` |

---

## SHARED / EITHER APP  (216 messages)

### `__init__`  (28)

| ✓ | Trigger | Message the user sees | Verdict | Issue | Source |
|---|---|---|---|---|---|
| ☐ | HTTP 403 | Admin access required | OK |  | `backend/dependencies/__init__.py:778` |
| ☐ | HTTP 403 | ERR_ACCOUNT_DELETED | FLAG | constant: ERR_ACCOUNT_DELETED | `backend/dependencies/__init__.py:153` |
| ☐ | HTTP 401 | ERR_ACCOUNT_INACTIVE | FLAG | constant: ERR_ACCOUNT_INACTIVE | `backend/dependencies/__init__.py:328` |
| ☐ | HTTP 401 | ERR_IDENTITY_INELIGIBLE | FLAG | constant: ERR_IDENTITY_INELIGIBLE | `backend/dependencies/__init__.py:423` |
| ☐ | HTTP 401 | ERR_IDLE_TIMEOUT | FLAG | constant: ERR_IDLE_TIMEOUT | `backend/dependencies/__init__.py:348` |
| ☐ | HTTP 401 | ERR_REACTIVATION_EXPIRED | FLAG | constant: ERR_REACTIVATION_EXPIRED | `backend/dependencies/__init__.py:177` |
| ☐ | HTTP 401 | ERR_SESSION_REVOKED | FLAG | constant: ERR_SESSION_REVOKED | `backend/dependencies/__init__.py:654` |
| ☐ | HTTP 401 | ERR_SESSION_REVOKED | FLAG | constant: ERR_SESSION_REVOKED | `backend/dependencies/__init__.py:541` |
| ☐ | HTTP 401 | ERR_SESSION_REVOKED | FLAG | constant: ERR_SESSION_REVOKED | `backend/dependencies/__init__.py:330` |
| ☐ | HTTP 401 | ERR_SESSION_REVOKED | FLAG | constant: ERR_SESSION_REVOKED | `backend/dependencies/__init__.py:459` |
| ☐ | HTTP 401 | ERR_TOKEN_AUDIENCE | FLAG | constant: ERR_TOKEN_AUDIENCE | `backend/dependencies/__init__.py:273` |
| ☐ | HTTP 401 | ERR_TOKEN_AUDIENCE | FLAG | constant: ERR_TOKEN_AUDIENCE | `backend/dependencies/__init__.py:276` |
| ☐ | HTTP 401 | ERR_TOKEN_AUDIENCE | FLAG | constant: ERR_TOKEN_AUDIENCE | `backend/dependencies/__init__.py:411` |
| ☐ | HTTP 401 | ERR_TOKEN_REVOKED | FLAG | constant: ERR_TOKEN_REVOKED | `backend/dependencies/__init__.py:302` |
| ☐ | HTTP 401 | ERR_TOKEN_REVOKED | FLAG | constant: ERR_TOKEN_REVOKED | `backend/dependencies/__init__.py:314` |
| ☐ | HTTP 401 | ERR_TOKEN_REVOKED | FLAG | constant: ERR_TOKEN_REVOKED | `backend/dependencies/__init__.py:323` |
| ☐ | HTTP 401 | ERR_TOKEN_REVOKED | FLAG | constant: ERR_TOKEN_REVOKED | `backend/dependencies/__init__.py:321` |
| ☐ | HTTP 401 | Firebase token has expired | OK |  | `backend/dependencies/__init__.py:394` |
| ☐ | HTTP 401 | Invalid token | OK |  | `backend/dependencies/__init__.py:181` |
| ☐ | HTTP 401 | Invalid token | OK |  | `backend/dependencies/__init__.py:184` |
| ☐ | HTTP 401 | Invalid token | OK |  | `backend/dependencies/__init__.py:499` |
| ☐ | HTTP 401 | Invalid token | OK |  | `backend/dependencies/__init__.py:129` |
| ☐ | HTTP 401 | Invalid token | OK |  | `backend/dependencies/__init__.py:179` |
| ☐ | HTTP 401 | Invalid token | OK |  | `backend/dependencies/__init__.py:494` |
| ☐ | HTTP 401 | No authorization token provided | OK |  | `backend/dependencies/__init__.py:386` |
| ☐ | HTTP 403 | This section is restricted to super admins. | OK |  | `backend/dependencies/__init__.py:824` |
| ☐ | HTTP 401 | Token has expired | OK |  | `backend/dependencies/__init__.py:127` |
| ☐ | HTTP 403 | You don't have access to this section. Ask a super admin to grant it. | OK |  | `backend/dependencies/__init__.py:803` |

### `addresses`  (2)

| ✓ | Trigger | Message the user sees | Verdict | Issue | Source |
|---|---|---|---|---|---|
| ☐ | HTTP 400 | Address and location don't match: {mismatch_reason} | OK |  | `backend/routes/addresses.py:39` |
| ☐ | HTTP 404 | Address not found | OK |  | `backend/routes/addresses.py:58` |

### `ai`  (3)

| ✓ | Trigger | Message the user sees | Verdict | Issue | Source |
|---|---|---|---|---|---|
| ☐ | HTTP 404 | Conversation not found | OK |  | `backend/routes/ai.py:254` |
| ☐ | HTTP 404 | Conversation not found | OK |  | `backend/routes/ai.py:262` |
| ☐ | HTTP 403 | Driver assistant is only available to driver accounts | OK |  | `backend/routes/ai.py:118` |

### `auth`  (8)

| ✓ | Trigger | Message the user sees | Verdict | Issue | Source |
|---|---|---|---|---|---|
| ☐ | HTTP 429 | A code was just sent — please wait a moment before requesting another | OK |  | `backend/routes/auth.py:212` |
| ☐ | HTTP 410 | ERR_ACCOUNT_DELETED | FLAG | constant: ERR_ACCOUNT_DELETED | `backend/routes/auth.py:1326` |
| ☐ | HTTP 410 | ERR_ACCOUNT_DELETED | FLAG | constant: ERR_ACCOUNT_DELETED | `backend/routes/auth.py:635` |
| ☐ | HTTP 403 | ERR_ACCOUNT_DELETED | FLAG | constant: ERR_ACCOUNT_DELETED | `backend/routes/auth.py:637` |
| ☐ | HTTP 410 | ERR_ACCOUNT_DELETED | FLAG | constant: ERR_ACCOUNT_DELETED | `backend/routes/auth.py:1545` |
| ☐ | HTTP 410 | ERR_ACCOUNT_DELETED | FLAG | constant: ERR_ACCOUNT_DELETED | `backend/routes/auth.py:1092` |
| ☐ | HTTP 429 | Too many code requests for this number — try again later | OK |  | `backend/routes/auth.py:222` |
| ☐ | HTTP 429 | Too many failed attempts — try again later | OK |  | `backend/routes/auth.py:251` |

### `disputes`  (9)

| ✓ | Trigger | Message the user sees | Verdict | Issue | Source |
|---|---|---|---|---|---|
| ☐ | HTTP 400 | A dispute is already open for this ride | OK |  | `backend/routes/disputes.py:80` |
| ☐ | HTTP 400 | Can only dispute completed or cancelled rides | OK |  | `backend/routes/disputes.py:69` |
| ☐ | HTTP 400 | Dispute already resolved | OK |  | `backend/routes/disputes.py:207` |
| ☐ | HTTP 404 | Dispute not found | OK |  | `backend/routes/disputes.py:135` |
| ☐ | HTTP 404 | Dispute not found | OK |  | `backend/routes/disputes.py:204` |
| ☐ | HTTP 403 | Not authorized | OK |  | `backend/routes/disputes.py:137` |
| ☐ | HTTP 403 | Not authorized for this ride | OK |  | `backend/routes/disputes.py:66` |
| ☐ | HTTP 400 | Refund amount ${req.refund_amount} exceeds original fare ${original_fare} | OK |  | `backend/routes/disputes.py:213` |
| ☐ | HTTP 404 | Ride not found | OK |  | `backend/routes/disputes.py:63` |

### `documents`  (23)

| ✓ | Trigger | Message the user sees | Verdict | Issue | Source |
|---|---|---|---|---|---|
| ☐ | HTTP 409 | Approved documents cannot be deleted. Upload a replacement to supersede it. | OK |  | `backend/documents.py:1024` |
| ☐ | HTTP 400 | Could not read the uploaded file | OK |  | `backend/documents.py:610` |
| ☐ | HTTP 404 | Document not found | OK |  | `backend/documents.py:1018` |
| ☐ | HTTP 404 | Document not found | OK |  | `backend/documents.py:1126` |
| ☐ | HTTP 404 | Driver profile not found | OK |  | `backend/documents.py:918` |
| ☐ | HTTP 404 | Driver profile not found | OK |  | `backend/documents.py:1012` |
| ☐ | HTTP 404 | Driver profile not found | OK |  | `backend/documents.py:1313` |
| ☐ | HTTP 400 | Empty file | OK |  | `backend/documents.py:613` |
| ☐ | HTTP 400 | Empty file | OK |  | `backend/documents.py:1236` |
| ☐ | HTTP 400 | File content does not match declared type | OK |  | `backend/documents.py:259` |
| ☐ | HTTP 400 | File content does not match declared type | OK |  | `backend/documents.py:246` |
| ☐ | HTTP 404 | File not found | OK |  | `backend/documents.py:1303` |
| ☐ | HTTP 400 | File type '{declared_type}' not allowed. Accepted: {', '.join(sorted(ALLOWED_MIME_TYPES))} | OK |  | `backend/documents.py:234` |
| ☐ | HTTP 400 | No fields to update | OK |  | `backend/documents.py:1072` |
| ☐ | HTTP 403 | Not authorized to access this document | OK |  | `backend/documents.py:1315` |
| ☐ | HTTP 403 | Not authorized to delete this document | OK |  | `backend/documents.py:1021` |
| ☐ | HTTP 404 | Requirement '{doc_data.requirement_id}' not found | OK |  | `backend/documents.py:846` |
| ☐ | HTTP 404 | Requirement not found | OK |  | `backend/documents.py:1076` |
| ☐ | HTTP 404 | Requirement not found | OK |  | `backend/documents.py:1090` |
| ☐ | HTTP 404 | Requirement not found | OK |  | `backend/documents.py:941` |
| ☐ | HTTP 400 | Status must be 'approved' or 'rejected' | OK |  | `backend/documents.py:1119` |
| ☐ | HTTP 400 | Unsupported file format (received '{declared_type}'). Please upload a JPG, PNG, GIF, WEBP or PDF. | OK |  | `backend/documents.py:216` |
| ☐ | HTTP 400 | {fmt} photos aren't supported. On iPhone, either take the photo with the in-app camera, or set Settings → Camera → Formats → Most Compatible and re-take it. JPG, PNG, GIF, WEBP and PDF are accepted. | OK |  | `backend/documents.py:172` |

### `favorites`  (5)

| ✓ | Trigger | Message the user sees | Verdict | Issue | Source |
|---|---|---|---|---|---|
| ☐ | HTTP 404 | Favorite not found | OK |  | `backend/routes/favorites.py:120` |
| ☐ | HTTP 404 | Favorite not found | OK |  | `backend/routes/favorites.py:137` |
| ☐ | HTTP 403 | Not authorized | OK |  | `backend/routes/favorites.py:154` |
| ☐ | HTTP 404 | Ride not found | OK |  | `backend/routes/favorites.py:152` |
| ☐ | HTTP 400 | {role.capitalize()} address and location don't match: {mismatch_reason} | OK |  | `backend/routes/favorites.py:92` |

### `features`  (24)

| ✓ | Trigger | Message the user sees | Verdict | Issue | Source |
|---|---|---|---|---|---|
| ☐ | HTTP 400 | Cannot add stops to completed/cancelled rides | OK |  | `backend/features.py:1120` |
| ☐ | HTTP 400 | Changing GST/PST/HST configuration requires a written justification (regulatory + financial risk). | OK |  | `backend/features.py:592` |
| ☐ | HTTP 400 | Choose a calculation mode: flat, per kilometre, or percentage. | OK |  | `backend/features.py:524` |
| ☐ | HTTP 400 | Choose a calculation mode: flat, per kilometre, or percentage. | OK |  | `backend/features.py:556` |
| ☐ | HTTP 403 | Not authorized to cancel this ride | OK |  | `backend/features.py:1100` |
| ☐ | HTTP 403 | Not authorized to modify this ride | OK |  | `backend/features.py:1118` |
| ☐ | HTTP 403 | Not authorized to share this ride | OK |  | `backend/features.py:1171` |
| ☐ | HTTP 403 | Not authorized to update this ride | OK |  | `backend/features.py:1146` |
| ☐ | HTTP 403 | Not authorized to view this ticket | OK |  | `backend/features.py:340` |
| ☐ | HTTP 400 | Only scheduled rides can be cancelled this way | OK |  | `backend/features.py:1102` |
| ☐ | HTTP 404 | Ride not found | OK |  | `backend/features.py:1098` |
| ☐ | HTTP 404 | Ride not found | OK |  | `backend/features.py:1116` |
| ☐ | HTTP 404 | Ride not found | OK |  | `backend/features.py:1144` |
| ☐ | HTTP 404 | Ride not found | OK |  | `backend/features.py:1169` |
| ☐ | HTTP 400 | Scheduled time must be at least 15 minutes from now. | OK |  | `backend/features.py:1036` |
| ☐ | HTTP 404 | Service area not found | OK |  | `backend/features.py:441` |
| ☐ | HTTP 404 | Service area not found | OK |  | `backend/features.py:520` |
| ☐ | HTTP 404 | Service area not found | OK |  | `backend/features.py:608` |
| ☐ | HTTP 404 | Service area not found | OK |  | `backend/features.py:624` |
| ☐ | HTTP 404 | Service area not found | OK |  | `backend/features.py:657` |
| ☐ | HTTP 404 | Stop not found | OK |  | `backend/features.py:1155` |
| ☐ | HTTP 404 | Ticket not found | OK |  | `backend/features.py:338` |
| ☐ | HTTP 404 | Trip not found or link expired | OK |  | `backend/features.py:1210` |
| ☐ | HTTP 400 | We couldn't read that pickup time. Please choose a date and time again. | OK |  | `backend/features.py:1031` |

### `google_places_new`  (2)

| ✓ | Trigger | Message the user sees | Verdict | Issue | Source |
|---|---|---|---|---|---|
| ☐ | HTTP 400 | location latitude/longitude out of range | OK |  | `backend/utils/google_places_new.py:63` |
| ☐ | HTTP 400 | location must be formatted as 'lat,lng' | OK |  | `backend/utils/google_places_new.py:57` |

### `guest_user_service`  (1)

| ✓ | Trigger | Message the user sees | Verdict | Issue | Source |
|---|---|---|---|---|---|
| ☐ | HTTP 409 | This phone number belongs to a deactivated account. | OK |  | `backend/services/guest_user_service.py:52` |

### `idempotency`  (2)

| ✓ | Trigger | Message the user sees | Verdict | Issue | Source |
|---|---|---|---|---|---|
| ☐ | HTTP 409 | A request with this Idempotency-Key is already in flight. Retry after a short delay. | OK |  | `backend/utils/idempotency.py:196` |
| ☐ | HTTP 400 | Idempotency-Key must be ≤{_MAX_KEY_LENGTH} characters | OK |  | `backend/utils/idempotency.py:157` |

### `legacy_consent`  (2)

| ✓ | Trigger | Message the user sees | Verdict | Issue | Source |
|---|---|---|---|---|---|
| ☐ | HTTP 404 | Not found | OK |  | `backend/routes/legacy_consent.py:91` |
| ☐ | HTTP 404 | User not found | OK |  | `backend/routes/legacy_consent.py:81` |

### `lost_and_found`  (14)

| ✓ | Trigger | Message the user sees | Verdict | Issue | Source |
|---|---|---|---|---|---|
| ☐ | HTTP 400 | Can only report items for completed rides | OK |  | `backend/routes/lost_and_found.py:269` |
| ☐ | HTTP 400 | Case is not awaiting driver response | OK |  | `backend/routes/lost_and_found.py:361` |
| ☐ | HTTP 404 | Case not found | OK |  | `backend/routes/lost_and_found.py:69` |
| ☐ | HTTP 404 | Case not found | OK |  | `backend/routes/lost_and_found.py:357` |
| ☐ | HTTP 403 | Driver profile not found | OK |  | `backend/routes/lost_and_found.py:261` |
| ☐ | HTTP 403 | Driver profile not found | OK |  | `backend/routes/lost_and_found.py:353` |
| ☐ | HTTP 400 | Empty file | OK |  | `backend/routes/lost_and_found.py:495` |
| ☐ | HTTP 403 | Not a participant in this case | OK |  | `backend/routes/lost_and_found.py:74` |
| ☐ | HTTP 403 | Not the driver on this case | OK |  | `backend/routes/lost_and_found.py:359` |
| ☐ | HTTP 400 | Only image attachments are supported | OK |  | `backend/routes/lost_and_found.py:502` |
| ☐ | HTTP 404 | Ride not found | OK |  | `backend/routes/lost_and_found.py:265` |
| ☐ | HTTP 400 | This case is closed | OK |  | `backend/routes/lost_and_found.py:441` |
| ☐ | HTTP 400 | This case is closed | OK |  | `backend/routes/lost_and_found.py:489` |
| ☐ | HTTP 403 | You were not the driver on this ride | OK |  | `backend/routes/lost_and_found.py:267` |

### `loyalty`  (4)

| ✓ | Trigger | Message the user sees | Verdict | Issue | Source |
|---|---|---|---|---|---|
| ☐ | HTTP 410 | Loyalty point redemption is currently unavailable. | OK |  | `backend/routes/loyalty.py:228` |
| ☐ | HTTP 403 | Not authorized | OK |  | `backend/routes/loyalty.py:128` |
| ☐ | HTTP 400 | Ride not completed | OK |  | `backend/routes/loyalty.py:130` |
| ☐ | HTTP 404 | Ride not found | OK |  | `backend/routes/loyalty.py:126` |

### `maps_proxy`  (2)

| ✓ | Trigger | Message the user sees | Verdict | Issue | Source |
|---|---|---|---|---|---|
| ☐ | HTTP 400 | {field} must be 'lat,lng' | OK |  | `backend/routes/maps_proxy.py:295` |
| ☐ | HTTP 400 | {field} out of range | OK |  | `backend/routes/maps_proxy.py:297` |

### `notifications`  (3)

| ✓ | Trigger | Message the user sees | Verdict | Issue | Source |
|---|---|---|---|---|---|
| ☐ | HTTP 404 | User {body.user_id} not found | OK |  | `backend/routes/notifications.py:168` |
| ☐ | HTTP 404 | User {target_user_id} not found | OK |  | `backend/routes/notifications.py:101` |
| ☐ | HTTP 400 | We couldn't tell which user to send this to. | OK |  | `backend/routes/notifications.py:97` |

### `password_policy`  (3)

| ✓ | Trigger | Message the user sees | Verdict | Issue | Source |
|---|---|---|---|---|---|
| ☐ | HTTP 422 | Admin passwords must be at least 20 characters long. | OK |  | `backend/utils/password_policy.py:166` |
| ☐ | HTTP 422 | Include at least one uppercase letter, one number, and one symbol. | OK |  | `backend/utils/password_policy.py:176` |
| ☐ | HTTP 422 | That password is too easy to guess. Please choose a less predictable one. | OK |  | `backend/utils/password_policy.py:182` |

### `quests`  (17)

| ✓ | Trigger | Message the user sees | Verdict | Issue | Source |
|---|---|---|---|---|---|
| ☐ | HTTP 400 | Already joined this quest | OK |  | `backend/routes/quests.py:211` |
| ☐ | HTTP 404 | Driver not found | OK |  | `backend/routes/quests.py:99` |
| ☐ | HTTP 404 | Driver not found | OK |  | `backend/routes/quests.py:186` |
| ☐ | HTTP 404 | Driver not found | OK |  | `backend/routes/quests.py:254` |
| ☐ | HTTP 404 | Driver not found | OK |  | `backend/routes/quests.py:308` |
| ☐ | HTTP 403 | Not authorized | OK |  | `backend/routes/quests.py:315` |
| ☐ | HTTP 400 | Quest has ended | OK |  | `backend/routes/quests.py:197` |
| ☐ | HTTP 400 | Quest is full | OK |  | `backend/routes/quests.py:222` |
| ☐ | HTTP 400 | Quest is no longer active | OK |  | `backend/routes/quests.py:193` |
| ☐ | HTTP 400 | Quest is not completed yet | OK |  | `backend/routes/quests.py:318` |
| ☐ | HTTP 404 | Quest not found | OK |  | `backend/routes/quests.py:190` |
| ☐ | HTTP 404 | Quest not found | OK |  | `backend/routes/quests.py:322` |
| ☐ | HTTP 404 | Quest not found | OK |  | `backend/routes/quests.py:503` |
| ☐ | HTTP 404 | Quest not found | OK |  | `backend/routes/quests.py:533` |
| ☐ | HTTP 404 | Quest progress not found | OK |  | `backend/routes/quests.py:312` |
| ☐ | HTTP 409 | Quest reward already claimed | OK |  | `backend/routes/quests.py:342` |
| ☐ | HTTP 403 | This quest isn't available in your service area | OK |  | `backend/routes/quests.py:206` |

### `safety`  (5)

| ✓ | Trigger | Message the user sees | Verdict | Issue | Source |
|---|---|---|---|---|---|
| ☐ | HTTP 400 | At most {MAX_INCIDENT_PHOTOS} photos per report | OK |  | `backend/routes/safety.py:181` |
| ☐ | HTTP 400 | Empty file | OK |  | `backend/routes/safety.py:187` |
| ☐ | HTTP 404 | Incident not found | OK |  | `backend/routes/safety.py:168` |
| ☐ | HTTP 403 | Not your report | OK |  | `backend/routes/safety.py:175` |
| ☐ | HTTP 400 | Only image attachments are supported | OK |  | `backend/routes/safety.py:192` |

### `server`  (2)

| ✓ | Trigger | Message the user sees | Verdict | Issue | Source |
|---|---|---|---|---|---|
| ☐ | HTTP 401 | Unauthorized | OK |  | `backend/server.py:446` |
| ☐ | HTTP 401 | Unauthorized | OK |  | `backend/server.py:323` |

### `users`  (19)

| ✓ | Trigger | Message the user sees | Verdict | Issue | Source |
|---|---|---|---|---|---|
| ☐ | HTTP 404 | Corporate account not found | OK |  | `backend/routes/users.py:726` |
| ☐ | HTTP 404 | Emergency contact not found | OK |  | `backend/routes/users.py:955` |
| ☐ | HTTP 400 | File must be an image (JPEG, PNG, WebP, or GIF) | OK |  | `backend/routes/users.py:677` |
| ☐ | HTTP 400 | Gender must be one of: {', '.join(valid_genders)} | OK |  | `backend/routes/users.py:107` |
| ☐ | HTTP 400 | Image must be smaller than 5MB | OK |  | `backend/routes/users.py:685` |
| ☐ | HTTP 400 | Invalid phone number | OK |  | `backend/routes/users.py:579` |
| ☐ | HTTP 400 | Invalid phone number for emergency contact | OK |  | `backend/routes/users.py:890` |
| ☐ | HTTP 404 | Invalid referral code | OK |  | `backend/routes/users.py:1130` |
| ☐ | HTTP 400 | Maximum {MAX_EMERGENCY_CONTACTS} emergency contacts allowed. Remove one before adding another. | OK |  | `backend/routes/users.py:883` |
| ☐ | HTTP 403 | Not a member of this corporate account | OK |  | `backend/routes/users.py:743` |
| ☐ | HTTP 400 | Phone number already in use | OK |  | `backend/routes/users.py:586` |
| ☐ | HTTP 400 | Referral code already applied | OK |  | `backend/routes/users.py:1107` |
| ☐ | HTTP 404 | User not found | OK |  | `backend/routes/users.py:1087` |
| ☐ | HTTP 404 | User not found | OK |  | `backend/routes/users.py:1096` |
| ☐ | HTTP 404 | User profile not found | OK |  | `backend/routes/users.py:99` |
| ☐ | HTTP 400 | You can't use your own referral code | OK |  | `backend/routes/users.py:1132` |
| ☐ | HTTP 409 | You have ${payable} in unpaid earnings. Please withdraw them before deleting your account. | OK |  | `backend/routes/users.py:378` |
| ☐ | HTTP 409 | You have a payout still processing. Please wait for it to complete before deleting your account. | OK |  | `backend/routes/users.py:359` |
| ☐ | HTTP 409 | You have a ride in progress. Please finish or cancel it before deleting your account. | OK |  | `backend/routes/users.py:348` |

### `validators`  (30)

| ✓ | Trigger | Message the user sees | Verdict | Issue | Source |
|---|---|---|---|---|---|
| ☐ | HTTP 400 | A required ID is missing. | OK |  | `backend/validators.py:252` |
| ☐ | HTTP 400 | Address is required | OK |  | `backend/validators.py:465` |
| ☐ | HTTP 400 | Address must be at least 10 characters | OK |  | `backend/validators.py:471` |
| ☐ | HTTP 400 | Address must contain alphanumeric characters | OK |  | `backend/validators.py:477` |
| ☐ | HTTP 400 | Amount cannot be zero | OK |  | `backend/validators.py:213` |
| ☐ | HTTP 400 | Amount must be a valid number | OK |  | `backend/validators.py:208` |
| ☐ | HTTP 400 | Amount must be at least {min_value} | OK |  | `backend/validators.py:218` |
| ☐ | HTTP 400 | Amount must not exceed {max_value} | OK |  | `backend/validators.py:223` |
| ☐ | HTTP 400 | Coordinates must be numeric values | OK |  | `backend/validators.py:154` |
| ☐ | HTTP 400 | Date must be after {min_date.isoformat()} | OK |  | `backend/validators.py:433` |
| ☐ | HTTP 400 | Date must be before {max_date.isoformat()} | OK |  | `backend/validators.py:438` |
| ☐ | HTTP 400 | Email address is required | OK |  | `backend/validators.py:94` |
| ☐ | HTTP 400 | Email address too long | OK |  | `backend/validators.py:106` |
| ☐ | HTTP 400 | Email local part too long | OK |  | `backend/validators.py:112` |
| ☐ | HTTP 400 | Future dates are not allowed | OK |  | `backend/validators.py:423` |
| ☐ | HTTP 400 | Invalid GPS coordinates: null island (0, 0) rejected | OK |  | `backend/validators.py:170` |
| ☐ | HTTP 400 | Invalid email address format | OK |  | `backend/validators.py:118` |
| ☐ | HTTP 400 | Invalid phone number. Spinr accepts Canadian and US numbers (+1). | OK |  | `backend/validators.py:65` |
| ☐ | HTTP 400 | Invalid {id_type} format | OK |  | `backend/validators.py:291` |
| ☐ | HTTP 400 | Latitude must be between -90 and 90 (got {lat}) | OK |  | `backend/validators.py:159` |
| ☐ | HTTP 400 | Longitude must be between -180 and 180 (got {lng}) | OK |  | `backend/validators.py:164` |
| ☐ | HTTP 400 | Past dates are not allowed | OK |  | `backend/validators.py:428` |
| ☐ | HTTP 400 | Phone number is required | OK |  | `backend/validators.py:41` |
| ☐ | HTTP 400 | Pickup and dropoff locations cannot be the same | OK |  | `backend/validators.py:528` |
| ☐ | HTTP 400 | String exceeds maximum length of {max_length} characters | OK |  | `backend/validators.py:342` |
| ☐ | HTTP 400 | String value cannot be empty | OK |  | `backend/validators.py:337` |
| ☐ | HTTP 400 | String value is required | OK |  | `backend/validators.py:325` |
| ☐ | HTTP 400 | That ID is not in a valid format. | OK |  | `backend/validators.py:260` |
| ☐ | HTTP 400 | We couldn't read that date and time. Please choose it again. | OK |  | `backend/validators.py:414` |
| ☐ | HTTP 400 | {id_type} is required | OK |  | `backend/validators.py:281` |

### `webhooks`  (6)

| ✓ | Trigger | Message the user sees | Verdict | Issue | Source |
|---|---|---|---|---|---|
| ☐ | HTTP 400 | Invalid payload | FLAG | technical term: payload | `backend/routes/webhooks.py:634` |
| ☐ | HTTP 400 | Invalid signature | OK |  | `backend/routes/webhooks.py:644` |
| ☐ | HTTP 400 | Missing event id | OK |  | `backend/routes/webhooks.py:660` |
| ☐ | HTTP 400 | invalid JSON | FLAG | technical term: json | `backend/routes/webhooks.py:2415` |
| ☐ | HTTP 403 | invalid SNS signature | OK |  | `backend/routes/webhooks.py:2426` |
| ☐ | HTTP 403 | unexpected SNS topic | OK |  | `backend/routes/webhooks.py:2430` |

### `worker`  (2)

| ✓ | Trigger | Message the user sees | Verdict | Issue | Source |
|---|---|---|---|---|---|
| ☐ | HTTP 401 | Unauthorized | OK |  | `backend/worker.py:191` |
| ☐ | HTTP 401 | Unauthorized | OK |  | `backend/worker.py:194` |

---

## CORPORATE PORTAL  (70 messages)

### `company_booking_service`  (3)

| ✓ | Trigger | Message the user sees | Verdict | Issue | Source |
|---|---|---|---|---|---|
| ☐ | HTTP 409 | This customer already has an active ride. | OK |  | `backend/services/company_booking_service.py:238` |
| ☐ | HTTP 403 | {'reason': 'allowance_low'} | OK |  | `backend/services/company_booking_service.py:130` |
| ☐ | HTTP 403 | {'reason': 'policy_violation', 'failed_rules': policy_result.failed_rules} | OK |  | `backend/services/company_booking_service.py:116` |

### `company_guard`  (2)

| ✓ | Trigger | Message the user sees | Verdict | Issue | Source |
|---|---|---|---|---|---|
| ☐ | HTTP 403 | not a company admin | OK |  | `backend/dependencies/company_guard.py:44` |
| ☐ | HTTP 403 | not a company member | OK |  | `backend/dependencies/company_guard.py:61` |

### `corporate_accounts`  (11)

| ✓ | Trigger | Message the user sees | Verdict | Issue | Source |
|---|---|---|---|---|---|
| ☐ | HTTP 404 | Corporate account disappeared mid-transition | OK |  | `backend/routes/corporate_accounts.py:869` |
| ☐ | HTTP 409 | Corporate account is closed and cannot be reopened | OK |  | `backend/routes/corporate_accounts.py:855` |
| ☐ | HTTP 404 | Corporate account not found | OK |  | `backend/routes/corporate_accounts.py:358` |
| ☐ | HTTP 404 | Corporate account not found | OK |  | `backend/routes/corporate_accounts.py:407` |
| ☐ | HTTP 404 | Corporate account not found | OK |  | `backend/routes/corporate_accounts.py:525` |
| ☐ | HTTP 404 | Corporate account not found | OK |  | `backend/routes/corporate_accounts.py:576` |
| ☐ | HTTP 404 | Corporate account not found | OK |  | `backend/routes/corporate_accounts.py:852` |
| ☐ | HTTP 400 | Invalid document path. | OK |  | `backend/routes/corporate_accounts.py:349` |
| ☐ | HTTP 404 | No KYB document on file | OK |  | `backend/routes/corporate_accounts.py:539` |
| ☐ | HTTP 400 | Unsupported content type for KYB | OK |  | `backend/routes/corporate_accounts.py:322` |
| ☐ | HTTP 400 | Upload not found — upload the document first. | OK |  | `backend/routes/corporate_accounts.py:354` |

### `corporate_company`  (21)

| ✓ | Trigger | Message the user sees | Verdict | Issue | Source |
|---|---|---|---|---|---|
| ☐ | HTTP 409 | Company is not active | OK |  | `backend/routes/corporate_company.py:1216` |
| ☐ | HTTP 404 | Company not found | OK |  | `backend/routes/corporate_company.py:1136` |
| ☐ | HTTP 404 | Company not found | OK |  | `backend/routes/corporate_company.py:1214` |
| ☐ | HTTP 404 | Member not found | OK |  | `backend/routes/corporate_company.py:442` |
| ☐ | HTTP 404 | Member not found | OK |  | `backend/routes/corporate_company.py:499` |
| ☐ | HTTP 404 | Member not found | OK |  | `backend/routes/corporate_company.py:531` |
| ☐ | HTTP 404 | Member not found | OK |  | `backend/routes/corporate_company.py:544` |
| ☐ | HTTP 404 | Member not found | OK |  | `backend/routes/corporate_company.py:566` |
| ☐ | HTTP 404 | Member not found | OK |  | `backend/routes/corporate_company.py:650` |
| ☐ | HTTP 422 | No payment method on file. Add a card, or save one as your default, then try again. | OK |  | `backend/routes/corporate_company.py:1242` |
| ☐ | HTTP 409 | Request already decided | OK |  | `backend/routes/corporate_company.py:652` |
| ☐ | HTTP 404 | Request not found | OK |  | `backend/routes/corporate_company.py:647` |
| ☐ | HTTP 404 | Section not found for this company | OK |  | `backend/routes/corporate_company.py:464` |
| ☐ | HTTP 404 | Wallet not found | OK |  | `backend/routes/corporate_company.py:1173` |
| ☐ | HTTP 422 | allowance request amount is required | OK |  | `backend/routes/corporate_company.py:662` |
| ☐ | HTTP 422 | each geofence feature must be an object | OK |  | `backend/routes/corporate_company.py:162` |
| ☐ | HTTP 422 | each geofence feature must have a geometry with type and coordinates | OK |  | `backend/routes/corporate_company.py:165` |
| ☐ | HTTP 422 | geofence must be a GeoJSON FeatureCollection | OK |  | `backend/routes/corporate_company.py:156` |
| ☐ | HTTP 422 | geofence.features must be a list | OK |  | `backend/routes/corporate_company.py:159` |
| ☐ | HTTP 409 | missing allowance or wallet | OK |  | `backend/routes/corporate_company.py:659` |
| ☐ | HTTP 422 | month must be YYYY-MM | OK |  | `backend/routes/corporate_company.py:861` |

### `corporate_company_bookings`  (7)

| ✓ | Trigger | Message the user sees | Verdict | Issue | Source |
|---|---|---|---|---|---|
| ☐ | HTTP 409 | A section with this name already exists. | OK |  | `backend/routes/corporate_company_bookings.py:461` |
| ☐ | HTTP 409 | A section with this name already exists. | OK |  | `backend/routes/corporate_company_bookings.py:500` |
| ☐ | HTTP 404 | Booking customer not found | OK |  | `backend/routes/corporate_company_bookings.py:288` |
| ☐ | HTTP 404 | Booking not found | OK |  | `backend/routes/corporate_company_bookings.py:282` |
| ☐ | HTTP 404 | Section not found | OK |  | `backend/routes/corporate_company_bookings.py:487` |
| ☐ | HTTP 404 | Section not found | OK |  | `backend/routes/corporate_company_bookings.py:525` |
| ☐ | HTTP 403 | You can only cancel your own bookings | OK |  | `backend/routes/corporate_company_bookings.py:284` |

### `corporate_company_kyb`  (6)

| ✓ | Trigger | Message the user sees | Verdict | Issue | Source |
|---|---|---|---|---|---|
| ☐ | HTTP 409 | 'Your account is suspended — contact support.' if state == 'suspended' else 'Verification is already complete for this company.' | OK |  | `backend/routes/corporate_company_kyb.py:126` |
| ☐ | HTTP 409 | 'Your account is suspended — contact support.' if state == 'suspended' else 'Verification is already complete for this company.' | OK |  | `backend/routes/corporate_company_kyb.py:164` |
| ☐ | HTTP 404 | Company not found | OK |  | `backend/routes/corporate_company_kyb.py:79` |
| ☐ | HTTP 415 | Document must be a PDF, PNG, or JPEG. | OK |  | `backend/routes/corporate_company_kyb.py:121` |
| ☐ | HTTP 400 | Invalid document path. | OK |  | `backend/routes/corporate_company_kyb.py:159` |
| ☐ | HTTP 400 | Upload not found — upload the document first. | OK |  | `backend/routes/corporate_company_kyb.py:179` |

### `corporate_policy_service`  (1)

| ✓ | Trigger | Message the user sees | Verdict | Issue | Source |
|---|---|---|---|---|---|
| ☐ | HTTP 403 | {'code': 'company_not_active', 'message': "Your company account isn't active right now. Contact your company admin, or use a personal payment method.", 'failed_rules': ['company_inactive']} | OK |  | `backend/services/corporate_policy_service.py:124` |

### `corporate_rider`  (8)

| ✓ | Trigger | Message the user sees | Verdict | Issue | Source |
|---|---|---|---|---|---|
| ☐ | HTTP 400 | Account has no email address; cannot join via domain | OK |  | `backend/routes/corporate_rider.py:161` |
| ☐ | HTTP 404 | Company not found | OK |  | `backend/routes/corporate_rider.py:336` |
| ☐ | HTTP 403 | ERR_EMAIL_UNVERIFIED | FLAG | constant: ERR_EMAIL_UNVERIFIED | `backend/routes/corporate_rider.py:153` |
| ☐ | HTTP 403 | Your email domain is not authorized for this company | OK |  | `backend/routes/corporate_rider.py:171` |
| ☐ | HTTP 409 | a request is already pending | OK |  | `backend/routes/corporate_rider.py:372` |
| ☐ | HTTP 409 | invite already used | OK |  | `backend/routes/corporate_rider.py:143` |
| ☐ | HTTP 404 | invite not found | OK |  | `backend/routes/corporate_rider.py:141` |
| ☐ | HTTP 403 | not a company member | OK |  | `backend/routes/corporate_rider.py:100` |

### `corporate_signup`  (2)

| ✓ | Trigger | Message the user sees | Verdict | Issue | Source |
|---|---|---|---|---|---|
| ☐ | HTTP 400 | Company signup requires a work-email session. Sign in via the business portal. | OK |  | `backend/routes/corporate_signup.py:77` |
| ☐ | HTTP 429 | You already have companies awaiting verification. Contact support to proceed. | OK |  | `backend/routes/corporate_signup.py:84` |

### `corporate_subscriptions`  (2)

| ✓ | Trigger | Message the user sees | Verdict | Issue | Source |
|---|---|---|---|---|---|
| ☐ | HTTP 403 | Corporate subscription billing is turned off. A super admin can enable the corporate_subscription_billing_enabled setting once it has been verified in staging. | FLAG | code identifier: corporate_subscription_billing_enabled | `backend/routes/corporate_subscriptions.py:137` |
| ☐ | HTTP 404 | We couldn't find that company account. | OK |  | `backend/routes/corporate_subscriptions.py:192` |

### `corporate_wallet`  (7)

| ✓ | Trigger | Message the user sees | Verdict | Issue | Source |
|---|---|---|---|---|---|
| ☐ | HTTP 409 | Company is not active | OK |  | `backend/routes/corporate_wallet.py:170` |
| ☐ | HTTP 404 | Company not found | OK |  | `backend/routes/corporate_wallet.py:168` |
| ☐ | HTTP 429 | Daily admin wallet-adjustment cap of ${cap} exceeded (${spent_today} already moved today, this call is ${abs(amount)}). Have a second admin process the remainder, or wait until tomorrow (UTC). | OK |  | `backend/routes/corporate_wallet.py:96` |
| ☐ | HTTP 422 | Set both a top-up threshold and a top-up amount before turning auto top-up on. | OK |  | `backend/routes/corporate_wallet.py:349` |
| ☐ | HTTP 404 | Wallet not found | OK |  | `backend/routes/corporate_wallet.py:136` |
| ☐ | HTTP 404 | Wallet not found | OK |  | `backend/routes/corporate_wallet.py:271` |
| ☐ | HTTP 404 | Wallet not found | OK |  | `backend/routes/corporate_wallet.py:336` |

---

## ALL APPS  (318 messages)

All 318 backend 5xx raise sites render the same phrase to the user: **"Internal server error"**.
The route-supplied text is discarded by `utils/error_handling.py::http_exception_handler`, so there is
nothing per-site to validate — only the single replacement phrase (finding F7). Sites listed for
completeness in the audit CSV rather than here.

## ADMIN (internal staff)  (1135 messages)

### `DataQualityScan`  (9)

| ✓ | Trigger | Message the user sees | Verdict | Issue | Source |
|---|---|---|---|---|---|
| ☐ | toast | ${res.rides_flagged ?? 0} ride(s) flagged for review.${                         conflicts > 0 ? | OK |  | `admin-dashboard/src/app/dashboard/bulk-operations/_components/DataQualityScan.tsx:93` |
| ☐ | toast | Commit failed | OK |  | `admin-dashboard/src/app/dashboard/bulk-operations/_components/DataQualityScan.tsx:102` |
| ☐ | toast | Could not build the scan plan. | OK |  | `admin-dashboard/src/app/dashboard/bulk-operations/_components/DataQualityScan.tsx:69` |
| ☐ | toast | Every finding was already flagged (likely by a prior run). | OK |  | `admin-dashboard/src/app/dashboard/bulk-operations/_components/DataQualityScan.tsx:87` |
| ☐ | toast | Flagged with some conflicts | OK |  | `admin-dashboard/src/app/dashboard/bulk-operations/_components/DataQualityScan.tsx:93` |
| ☐ | toast | Nothing left to flag | OK |  | `admin-dashboard/src/app/dashboard/bulk-operations/_components/DataQualityScan.tsx:87` |
| ☐ | toast | Preview failed | OK |  | `admin-dashboard/src/app/dashboard/bulk-operations/_components/DataQualityScan.tsx:69` |
| ☐ | toast | Summary copied | OK |  | `admin-dashboard/src/app/dashboard/bulk-operations/_components/DataQualityScan.tsx:207` |
| ☐ | toast | The scan commit did not complete. | OK |  | `admin-dashboard/src/app/dashboard/bulk-operations/_components/DataQualityScan.tsx:102` |

### `DriverDormancyFlag`  (9)

| ✓ | Trigger | Message the user sees | Verdict | Issue | Source |
|---|---|---|---|---|---|
| ☐ | toast | ${res.drivers_flagged ?? 0} driver(s) flagged.${                         conflicts > 0 ? | OK |  | `admin-dashboard/src/app/dashboard/bulk-operations/_components/DriverDormancyFlag.tsx:111` |
| ☐ | toast | Commit failed | OK |  | `admin-dashboard/src/app/dashboard/bulk-operations/_components/DriverDormancyFlag.tsx:120` |
| ☐ | toast | Could not build the flag plan. | OK |  | `admin-dashboard/src/app/dashboard/bulk-operations/_components/DriverDormancyFlag.tsx:86` |
| ☐ | toast | Every matching driver was already flagged (likely by a prior run). | OK |  | `admin-dashboard/src/app/dashboard/bulk-operations/_components/DriverDormancyFlag.tsx:104` |
| ☐ | toast | Flagged with some conflicts | OK |  | `admin-dashboard/src/app/dashboard/bulk-operations/_components/DriverDormancyFlag.tsx:111` |
| ☐ | toast | Nothing left to flag | OK |  | `admin-dashboard/src/app/dashboard/bulk-operations/_components/DriverDormancyFlag.tsx:104` |
| ☐ | toast | Preview failed | OK |  | `admin-dashboard/src/app/dashboard/bulk-operations/_components/DriverDormancyFlag.tsx:86` |
| ☐ | toast | Summary copied | OK |  | `admin-dashboard/src/app/dashboard/bulk-operations/_components/DriverDormancyFlag.tsx:222` |
| ☐ | toast | The flag commit did not complete. | OK |  | `admin-dashboard/src/app/dashboard/bulk-operations/_components/DriverDormancyFlag.tsx:120` |

### `DriverRepairPass`  (9)

| ✓ | Trigger | Message the user sees | Verdict | Issue | Source |
|---|---|---|---|---|---|
| ☐ | toast | ${res.rides_repaired ?? 0} ride(s) repaired, ${                         res.drivers_recounted ?? 0                     } driver(s) recounted.${                         conflicts > 0 ? | OK |  | `admin-dashboard/src/app/dashboard/bulk-operations/_components/DriverRepairPass.tsx:106` |
| ☐ | toast | Commit failed | OK |  | `admin-dashboard/src/app/dashboard/bulk-operations/_components/DriverRepairPass.tsx:117` |
| ☐ | toast | Could not build the repair plan. | OK |  | `admin-dashboard/src/app/dashboard/bulk-operations/_components/DriverRepairPass.tsx:81` |
| ☐ | toast | Every repairable ride was already repaired (likely by a prior run). | OK |  | `admin-dashboard/src/app/dashboard/bulk-operations/_components/DriverRepairPass.tsx:99` |
| ☐ | toast | Nothing left to repair | OK |  | `admin-dashboard/src/app/dashboard/bulk-operations/_components/DriverRepairPass.tsx:99` |
| ☐ | toast | Preview failed | OK |  | `admin-dashboard/src/app/dashboard/bulk-operations/_components/DriverRepairPass.tsx:81` |
| ☐ | toast | Repaired with some conflicts | OK |  | `admin-dashboard/src/app/dashboard/bulk-operations/_components/DriverRepairPass.tsx:106` |
| ☐ | toast | Summary copied | OK |  | `admin-dashboard/src/app/dashboard/bulk-operations/_components/DriverRepairPass.tsx:220` |
| ☐ | toast | The repair commit did not complete. | OK |  | `admin-dashboard/src/app/dashboard/bulk-operations/_components/DriverRepairPass.tsx:117` |

### `DurationEstimatedBackfill`  (9)

| ✓ | Trigger | Message the user sees | Verdict | Issue | Source |
|---|---|---|---|---|---|
| ☐ | toast | ${res.updated ?? 0} ride(s) stamped.${                         conflicts > 0 ? | OK |  | `admin-dashboard/src/app/dashboard/bulk-operations/_components/DurationEstimatedBackfill.tsx:93` |
| ☐ | toast | Commit failed | OK |  | `admin-dashboard/src/app/dashboard/bulk-operations/_components/DurationEstimatedBackfill.tsx:102` |
| ☐ | toast | Could not build the backfill plan. | OK |  | `admin-dashboard/src/app/dashboard/bulk-operations/_components/DurationEstimatedBackfill.tsx:69` |
| ☐ | toast | Every legacy ride was already marked (likely by a prior run). | OK |  | `admin-dashboard/src/app/dashboard/bulk-operations/_components/DurationEstimatedBackfill.tsx:87` |
| ☐ | toast | Nothing left to stamp | OK |  | `admin-dashboard/src/app/dashboard/bulk-operations/_components/DurationEstimatedBackfill.tsx:87` |
| ☐ | toast | Preview failed | OK |  | `admin-dashboard/src/app/dashboard/bulk-operations/_components/DurationEstimatedBackfill.tsx:69` |
| ☐ | toast | Stamped with some conflicts | OK |  | `admin-dashboard/src/app/dashboard/bulk-operations/_components/DurationEstimatedBackfill.tsx:93` |
| ☐ | toast | Summary copied | OK |  | `admin-dashboard/src/app/dashboard/bulk-operations/_components/DurationEstimatedBackfill.tsx:206` |
| ☐ | toast | The backfill commit did not complete. | OK |  | `admin-dashboard/src/app/dashboard/bulk-operations/_components/DurationEstimatedBackfill.tsx:102` |

### `EntitySearchTable`  (2)

| ✓ | Trigger | Message the user sees | Verdict | Issue | Source |
|---|---|---|---|---|---|
| ☐ | toast | Search failed | OK |  | `admin-dashboard/src/components/data-transfer/EntitySearchTable.tsx:111` |
| ☐ | toast | Unknown error | OK |  | `admin-dashboard/src/components/data-transfer/EntitySearchTable.tsx:111` |

### `ExportTab`  (16)

| ✓ | Trigger | Message the user sees | Verdict | Issue | Source |
|---|---|---|---|---|---|
| ☐ | toast | Approval required | OK |  | `admin-dashboard/src/app/dashboard/data-transfer/ExportTab.tsx:200` |
| ☐ | toast | Download starting… | OK |  | `admin-dashboard/src/app/dashboard/data-transfer/ExportTab.tsx:225` |
| ☐ | toast | Download starting… Note: document metadata only — no files (see README.txt in the ZIP). | OK |  | `admin-dashboard/src/app/dashboard/data-transfer/ExportTab.tsx:225` |
| ☐ | toast | Export failed | OK |  | `admin-dashboard/src/app/dashboard/data-transfer/ExportTab.tsx:216` |
| ☐ | toast | Export failed | OK |  | `admin-dashboard/src/app/dashboard/data-transfer/ExportTab.tsx:239` |
| ☐ | toast | Export queued | OK |  | `admin-dashboard/src/app/dashboard/data-transfer/ExportTab.tsx:209` |
| ☐ | toast | Export ready | OK |  | `admin-dashboard/src/app/dashboard/data-transfer/ExportTab.tsx:225` |
| ☐ | toast | Nothing to export | OK |  | `admin-dashboard/src/app/dashboard/data-transfer/ExportTab.tsx:176` |
| ☐ | toast | Only the first ${MAX_ENTITIES_PER_EXPORT} matching records were exported — narrow your filter to export the rest in a separate batch. | OK |  | `admin-dashboard/src/app/dashboard/data-transfer/ExportTab.tsx:180` |
| ☐ | toast | Preparing ${job.requested_count} record(s)… this may take a moment for large batches. | OK |  | `admin-dashboard/src/app/dashboard/data-transfer/ExportTab.tsx:209` |
| ☐ | toast | Select at least one record first. | OK |  | `admin-dashboard/src/app/dashboard/data-transfer/ExportTab.tsx:176` |
| ☐ | toast | Selection truncated | OK |  | `admin-dashboard/src/app/dashboard/data-transfer/ExportTab.tsx:180` |
| ☐ | toast | This export needs a different admin's approval before it can run — it's been added to the Export Approvals queue. | OK |  | `admin-dashboard/src/app/dashboard/data-transfer/ExportTab.tsx:200` |
| ☐ | toast | Unknown error | OK |  | `admin-dashboard/src/app/dashboard/data-transfer/ExportTab.tsx:216` |
| ☐ | toast | Unknown error | OK |  | `admin-dashboard/src/app/dashboard/data-transfer/ExportTab.tsx:239` |
| ☐ | toast | no files in the ZIP | OK |  | `admin-dashboard/src/app/dashboard/data-transfer/ExportTab.tsx:225` |

### `ImportTab`  (8)

| ✓ | Trigger | Message the user sees | Verdict | Issue | Source |
|---|---|---|---|---|---|
| ☐ | toast | ${result.created_users ?? 0} user(s), ${result.created_drivers ?? 0} driver(s), ${result.documents_replayed ?? 0} document(s) created${updateSummary}. | OK |  | `admin-dashboard/src/app/dashboard/data-transfer/ImportTab.tsx:55` |
| ☐ | toast | Commit failed | OK |  | `admin-dashboard/src/app/dashboard/data-transfer/ImportTab.tsx:67` |
| ☐ | toast | Commit refused | OK |  | `admin-dashboard/src/app/dashboard/data-transfer/ImportTab.tsx:60` |
| ☐ | toast | Import committed | OK |  | `admin-dashboard/src/app/dashboard/data-transfer/ImportTab.tsx:55` |
| ☐ | toast | The bundle no longer validates cleanly — see the errors below. | OK |  | `admin-dashboard/src/app/dashboard/data-transfer/ImportTab.tsx:60` |
| ☐ | toast | Unknown error | OK |  | `admin-dashboard/src/app/dashboard/data-transfer/ImportTab.tsx:38` |
| ☐ | toast | Unknown error | OK |  | `admin-dashboard/src/app/dashboard/data-transfer/ImportTab.tsx:67` |
| ☐ | toast | Validation failed | OK |  | `admin-dashboard/src/app/dashboard/data-transfer/ImportTab.tsx:38` |

### `JobsTab`  (4)

| ✓ | Trigger | Message the user sees | Verdict | Issue | Source |
|---|---|---|---|---|---|
| ☐ | toast | Download failed | OK |  | `admin-dashboard/src/app/dashboard/data-transfer/JobsTab.tsx:50` |
| ☐ | toast | Failed to load jobs | OK |  | `admin-dashboard/src/app/dashboard/data-transfer/JobsTab.tsx:33` |
| ☐ | toast | Unknown error | OK |  | `admin-dashboard/src/app/dashboard/data-transfer/JobsTab.tsx:33` |
| ☐ | toast | Unknown error | OK |  | `admin-dashboard/src/app/dashboard/data-transfer/JobsTab.tsx:50` |

### `LegacyBookingImport`  (9)

| ✓ | Trigger | Message the user sees | Verdict | Issue | Source |
|---|---|---|---|---|---|
| ☐ | toast | ${res.imported_rides ?? 0} ride(s) and ${res.offset_payouts ?? 0} offset payout(s) written. | OK |  | `admin-dashboard/src/app/dashboard/bulk-operations/_components/LegacyBookingImport.tsx:226` |
| ☐ | toast | Commit failed | OK |  | `admin-dashboard/src/app/dashboard/bulk-operations/_components/LegacyBookingImport.tsx:232` |
| ☐ | toast | Could not validate the export. | OK |  | `admin-dashboard/src/app/dashboard/bulk-operations/_components/LegacyBookingImport.tsx:193` |
| ☐ | toast | Import committed | OK |  | `admin-dashboard/src/app/dashboard/bulk-operations/_components/LegacyBookingImport.tsx:226` |
| ☐ | toast | Import refused | OK |  | `admin-dashboard/src/app/dashboard/bulk-operations/_components/LegacyBookingImport.tsx:218` |
| ☐ | toast | Nothing was written. The report below shows why — most often the rows were already imported. | OK |  | `admin-dashboard/src/app/dashboard/bulk-operations/_components/LegacyBookingImport.tsx:218` |
| ☐ | toast | Summary copied | OK |  | `admin-dashboard/src/app/dashboard/bulk-operations/_components/LegacyBookingImport.tsx:463` |
| ☐ | toast | The import did not complete. | OK |  | `admin-dashboard/src/app/dashboard/bulk-operations/_components/LegacyBookingImport.tsx:232` |
| ☐ | toast | Validation failed | OK |  | `admin-dashboard/src/app/dashboard/bulk-operations/_components/LegacyBookingImport.tsx:193` |

### `LegacyIdCrosswalkBackfill`  (9)

| ✓ | Trigger | Message the user sees | Verdict | Issue | Source |
|---|---|---|---|---|---|
| ☐ | toast | ${res.written ?? 0} crosswalk row(s) written.${                         failed > 0 ? | OK |  | `admin-dashboard/src/app/dashboard/bulk-operations/_components/LegacyIdCrosswalkBackfill.tsx:119` |
| ☐ | toast | Commit failed | OK |  | `admin-dashboard/src/app/dashboard/bulk-operations/_components/LegacyIdCrosswalkBackfill.tsx:128` |
| ☐ | toast | Could not build the crosswalk plan. | OK |  | `admin-dashboard/src/app/dashboard/bulk-operations/_components/LegacyIdCrosswalkBackfill.tsx:90` |
| ☐ | toast | Every id was already recorded (likely by a prior run). | OK |  | `admin-dashboard/src/app/dashboard/bulk-operations/_components/LegacyIdCrosswalkBackfill.tsx:113` |
| ☐ | toast | Nothing left to record | OK |  | `admin-dashboard/src/app/dashboard/bulk-operations/_components/LegacyIdCrosswalkBackfill.tsx:113` |
| ☐ | toast | Preview failed | OK |  | `admin-dashboard/src/app/dashboard/bulk-operations/_components/LegacyIdCrosswalkBackfill.tsx:90` |
| ☐ | toast | Recorded with some failures | OK |  | `admin-dashboard/src/app/dashboard/bulk-operations/_components/LegacyIdCrosswalkBackfill.tsx:119` |
| ☐ | toast | Summary copied | OK |  | `admin-dashboard/src/app/dashboard/bulk-operations/_components/LegacyIdCrosswalkBackfill.tsx:229` |
| ☐ | toast | The crosswalk commit did not complete. | OK |  | `admin-dashboard/src/app/dashboard/bulk-operations/_components/LegacyIdCrosswalkBackfill.tsx:128` |

### `LegacyTaxIdImport`  (11)

| ✓ | Trigger | Message the user sees | Verdict | Issue | Source |
|---|---|---|---|---|---|
| ☐ | toast | ${res.written_sin ?? 0} SIN, ${res.written_gst ?? 0} GST BN written. | OK |  | `admin-dashboard/src/app/dashboard/bulk-operations/_components/LegacyTaxIdImport.tsx:332` |
| ☐ | toast | Backfill committed | OK |  | `admin-dashboard/src/app/dashboard/bulk-operations/_components/LegacyTaxIdImport.tsx:332` |
| ☐ | toast | Commit failed | OK |  | `admin-dashboard/src/app/dashboard/bulk-operations/_components/LegacyTaxIdImport.tsx:338` |
| ☐ | toast | Could not join and validate banks.csv + drivers.csv. | OK |  | `admin-dashboard/src/app/dashboard/bulk-operations/_components/LegacyTaxIdImport.tsx:276` |
| ☐ | toast | Could not validate the CSV. | OK |  | `admin-dashboard/src/app/dashboard/bulk-operations/_components/LegacyTaxIdImport.tsx:252` |
| ☐ | toast | Import refused | OK |  | `admin-dashboard/src/app/dashboard/bulk-operations/_components/LegacyTaxIdImport.tsx:304` |
| ☐ | toast | Preparation failed | OK |  | `admin-dashboard/src/app/dashboard/bulk-operations/_components/LegacyTaxIdImport.tsx:276` |
| ☐ | toast | Summary copied | OK |  | `admin-dashboard/src/app/dashboard/bulk-operations/_components/LegacyTaxIdImport.tsx:530` |
| ☐ | toast | The backfill did not complete. | OK |  | `admin-dashboard/src/app/dashboard/bulk-operations/_components/LegacyTaxIdImport.tsx:338` |
| ☐ | toast | This no longer validates — fix the errors below and try again. | OK |  | `admin-dashboard/src/app/dashboard/bulk-operations/_components/LegacyTaxIdImport.tsx:304` |
| ☐ | toast | Validation failed | OK |  | `admin-dashboard/src/app/dashboard/bulk-operations/_components/LegacyTaxIdImport.tsx:252` |

### `LegacyWalletImport`  (10)

| ✓ | Trigger | Message the user sees | Verdict | Issue | Source |
|---|---|---|---|---|---|
| ☐ | toast | ${res.applied ?? 0} applied, ${res.deduped ?? 0} already applied, ${failed} failed. | OK |  | `admin-dashboard/src/app/dashboard/bulk-operations/_components/LegacyWalletImport.tsx:203` |
| ☐ | toast | Commit failed | OK |  | `admin-dashboard/src/app/dashboard/bulk-operations/_components/LegacyWalletImport.tsx:210` |
| ☐ | toast | Could not validate the export. | OK |  | `admin-dashboard/src/app/dashboard/bulk-operations/_components/LegacyWalletImport.tsx:169` |
| ☐ | toast | Import committed | OK |  | `admin-dashboard/src/app/dashboard/bulk-operations/_components/LegacyWalletImport.tsx:203` |
| ☐ | toast | Import completed with failures | OK |  | `admin-dashboard/src/app/dashboard/bulk-operations/_components/LegacyWalletImport.tsx:203` |
| ☐ | toast | Import refused | OK |  | `admin-dashboard/src/app/dashboard/bulk-operations/_components/LegacyWalletImport.tsx:194` |
| ☐ | toast | Nothing was written. The report below shows why — most often the rows were already applied. | OK |  | `admin-dashboard/src/app/dashboard/bulk-operations/_components/LegacyWalletImport.tsx:194` |
| ☐ | toast | Summary copied | OK |  | `admin-dashboard/src/app/dashboard/bulk-operations/_components/LegacyWalletImport.tsx:359` |
| ☐ | toast | The import did not complete. | OK |  | `admin-dashboard/src/app/dashboard/bulk-operations/_components/LegacyWalletImport.tsx:210` |
| ☐ | toast | Validation failed | OK |  | `admin-dashboard/src/app/dashboard/bulk-operations/_components/LegacyWalletImport.tsx:169` |

### `MigrationChecklist`  (1)

| ✓ | Trigger | Message the user sees | Verdict | Issue | Source |
|---|---|---|---|---|---|
| ☐ | setError | Could not load migration status | OK |  | `admin-dashboard/src/app/dashboard/bulk-operations/_components/MigrationChecklist.tsx:152` |

### `PreLaunchDataFlag`  (9)

| ✓ | Trigger | Message the user sees | Verdict | Issue | Source |
|---|---|---|---|---|---|
| ☐ | toast | ${res.drivers_flagged ?? 0} driver(s), ${res.riders_flagged ?? 0} rider(s), ${                         res.rides_flagged ?? 0                     } ride(s) flagged.${                         conflicts > 0 ? | OK |  | `admin-dashboard/src/app/dashboard/bulk-operations/_components/PreLaunchDataFlag.tsx:109` |
| ☐ | toast | Commit failed | OK |  | `admin-dashboard/src/app/dashboard/bulk-operations/_components/PreLaunchDataFlag.tsx:120` |
| ☐ | toast | Could not build the flag plan. | OK |  | `admin-dashboard/src/app/dashboard/bulk-operations/_components/PreLaunchDataFlag.tsx:83` |
| ☐ | toast | Every matching row was already flagged (likely by a prior run). | OK |  | `admin-dashboard/src/app/dashboard/bulk-operations/_components/PreLaunchDataFlag.tsx:101` |
| ☐ | toast | Flagged with some conflicts | OK |  | `admin-dashboard/src/app/dashboard/bulk-operations/_components/PreLaunchDataFlag.tsx:109` |
| ☐ | toast | Nothing left to flag | OK |  | `admin-dashboard/src/app/dashboard/bulk-operations/_components/PreLaunchDataFlag.tsx:101` |
| ☐ | toast | Preview failed | OK |  | `admin-dashboard/src/app/dashboard/bulk-operations/_components/PreLaunchDataFlag.tsx:83` |
| ☐ | toast | Summary copied | OK |  | `admin-dashboard/src/app/dashboard/bulk-operations/_components/PreLaunchDataFlag.tsx:219` |
| ☐ | toast | The flag commit did not complete. | OK |  | `admin-dashboard/src/app/dashboard/bulk-operations/_components/PreLaunchDataFlag.tsx:120` |

### `SgiFormsTab`  (24)

| ✓ | Trigger | Message the user sees | Verdict | Issue | Source |
|---|---|---|---|---|---|
| ☐ | toast | ${checksMissing} driver(s) have no criminal record check | OK |  | `admin-dashboard/src/app/dashboard/data-transfer/SgiFormsTab.tsx:248` |
| ☐ | toast | ${successes} form(s) generated | OK |  | `admin-dashboard/src/app/dashboard/data-transfer/SgiFormsTab.tsx:195` |
| ☐ | toast | Both forms and ${checksIncluded} criminal record check(s). Download starting… | OK |  | `admin-dashboard/src/app/dashboard/data-transfer/SgiFormsTab.tsx:255` |
| ☐ | toast | Check at least one form (D00032 and/or D00033). | OK |  | `admin-dashboard/src/app/dashboard/data-transfer/SgiFormsTab.tsx:273` |
| ☐ | toast | Download(s) starting… | OK |  | `admin-dashboard/src/app/dashboard/data-transfer/SgiFormsTab.tsx:195` |
| ☐ | toast | Form generation failed | OK |  | `admin-dashboard/src/app/dashboard/data-transfer/SgiFormsTab.tsx:198` |
| ☐ | toast | Form generation failed | OK |  | `admin-dashboard/src/app/dashboard/data-transfer/SgiFormsTab.tsx:208` |
| ☐ | toast | Form generation failed | OK |  | `admin-dashboard/src/app/dashboard/data-transfer/SgiFormsTab.tsx:288` |
| ☐ | toast | No drivers selected | OK |  | `admin-dashboard/src/app/dashboard/data-transfer/SgiFormsTab.tsx:227` |
| ☐ | toast | No drivers selected | OK |  | `admin-dashboard/src/app/dashboard/data-transfer/SgiFormsTab.tsx:293` |
| ☐ | toast | No form selected | OK |  | `admin-dashboard/src/app/dashboard/data-transfer/SgiFormsTab.tsx:273` |
| ☐ | toast | Only the first ${MAX_DOCUMENT_BUNDLE_DRIVERS} matching drivers are included — narrow the filter to file the rest separately. | OK |  | `admin-dashboard/src/app/dashboard/data-transfer/SgiFormsTab.tsx:231` |
| ☐ | toast | Only the first ${MAX_ROW_LIMIT} matching drivers were considered — narrow your filter for a complete submission. | OK |  | `admin-dashboard/src/app/dashboard/data-transfer/SgiFormsTab.tsx:282` |
| ☐ | toast | Package download failed | OK |  | `admin-dashboard/src/app/dashboard/data-transfer/SgiFormsTab.tsx:261` |
| ☐ | toast | Select at least one driver first. | OK |  | `admin-dashboard/src/app/dashboard/data-transfer/SgiFormsTab.tsx:227` |
| ☐ | toast | Select driver records in Search & Select first (rider selections don't apply). | OK |  | `admin-dashboard/src/app/dashboard/data-transfer/SgiFormsTab.tsx:293` |
| ☐ | toast | Selection truncated | OK |  | `admin-dashboard/src/app/dashboard/data-transfer/SgiFormsTab.tsx:231` |
| ☐ | toast | Selection truncated | OK |  | `admin-dashboard/src/app/dashboard/data-transfer/SgiFormsTab.tsx:282` |
| ☐ | toast | Submission package ready | OK |  | `admin-dashboard/src/app/dashboard/data-transfer/SgiFormsTab.tsx:255` |
| ☐ | toast | The forms are in the ZIP, but those drivers' checks are missing — see MISSING_CRIMINAL_RECORD_CHECKS.txt before filing. | FLAG | constant: MISSING_CRIMINAL_RECORD_CHECKS | `admin-dashboard/src/app/dashboard/data-transfer/SgiFormsTab.tsx:248` |
| ☐ | toast | Unknown error | OK |  | `admin-dashboard/src/app/dashboard/data-transfer/SgiFormsTab.tsx:198` |
| ☐ | toast | Unknown error | OK |  | `admin-dashboard/src/app/dashboard/data-transfer/SgiFormsTab.tsx:208` |
| ☐ | toast | Unknown error | OK |  | `admin-dashboard/src/app/dashboard/data-transfer/SgiFormsTab.tsx:261` |
| ☐ | toast | Unknown error | OK |  | `admin-dashboard/src/app/dashboard/data-transfer/SgiFormsTab.tsx:288` |

### `ai_console`  (5)

| ✓ | Trigger | Message the user sees | Verdict | Issue | Source |
|---|---|---|---|---|---|
| ☐ | HTTP 403 | AI console is restricted to super admins | OK |  | `backend/routes/admin/ai_console.py:68` |
| ☐ | HTTP 404 | Conversation not found | OK |  | `backend/routes/admin/ai_console.py:175` |
| ☐ | HTTP 404 | User not found | OK |  | `backend/routes/admin/ai_console.py:97` |
| ☐ | HTTP 422 | severity must be one of: high, critical | OK |  | `backend/routes/admin/ai_console.py:202` |
| ☐ | HTTP 422 | source must be one of: message, tool | OK |  | `backend/routes/admin/ai_console.py:204` |

### `allowance-dialog`  (1)

| ✓ | Trigger | Message the user sees | Verdict | Issue | Source |
|---|---|---|---|---|---|
| ☐ | setError | Save failed | OK |  | `admin-dashboard/src/app/dashboard/corporate-accounts/[id]/members/allowance-dialog.tsx:91` |

### `auth`  (51)

| ✓ | Trigger | Message the user sees | Verdict | Issue | Source |
|---|---|---|---|---|---|
| ☐ | HTTP 403 | Account is deactivated | OK |  | `backend/routes/admin/auth.py:373` |
| ☐ | HTTP 423 | Account locked due to too many failed login attempts. Try again in 24 hours. | OK |  | `backend/routes/admin/auth.py:323` |
| ☐ | HTTP 401 | Account not found or inactive | OK |  | `backend/routes/admin/auth.py:901` |
| ☐ | HTTP 401 | Account not found or inactive | OK |  | `backend/routes/admin/auth.py:1119` |
| ☐ | HTTP 429 | Break-glass rate limit exceeded for today | OK |  | `backend/routes/admin/auth.py:1261` |
| ☐ | HTTP 400 | Current password is incorrect | OK |  | `backend/routes/admin/auth.py:705` |
| ☐ | HTTP 400 | Incorrect password | OK |  | `backend/routes/admin/auth.py:1076` |
| ☐ | HTTP 401 | Invalid MFA token | OK |  | `backend/routes/admin/auth.py:1116` |
| ☐ | HTTP 401 | Invalid MFA token | OK |  | `backend/routes/admin/auth.py:1123` |
| ☐ | HTTP 401 | Invalid MFA token | OK |  | `backend/routes/admin/auth.py:1111` |
| ☐ | HTTP 400 | Invalid TOTP code | OK |  | `backend/routes/admin/auth.py:999` |
| ☐ | HTTP 400 | Invalid TOTP code | OK |  | `backend/routes/admin/auth.py:1078` |
| ☐ | HTTP 400 | Invalid TOTP code | OK |  | `backend/routes/admin/auth.py:713` |
| ☐ | HTTP 401 | Invalid TOTP code or backup code | OK |  | `backend/routes/admin/auth.py:1140` |
| ☐ | HTTP 401 | Invalid auth scheme | OK |  | `backend/routes/admin/auth.py:599` |
| ☐ | HTTP 401 | Invalid auth scheme | OK |  | `backend/routes/admin/auth.py:856` |
| ☐ | HTTP 401 | Invalid auth scheme | OK |  | `backend/routes/admin/auth.py:938` |
| ☐ | HTTP 401 | Invalid break-glass token | OK |  | `backend/routes/admin/auth.py:1282` |
| ☐ | HTTP 401 | Invalid credentials | OK |  | `backend/routes/admin/auth.py:448` |
| ☐ | HTTP 401 | Invalid refresh token | OK |  | `backend/routes/admin/auth.py:462` |
| ☐ | HTTP 401 | Invalid refresh token | OK |  | `backend/routes/admin/auth.py:466` |
| ☐ | HTTP 401 | Invalid refresh token | OK |  | `backend/routes/admin/auth.py:501` |
| ☐ | HTTP 401 | Invalid token | OK |  | `backend/routes/admin/auth.py:874` |
| ☐ | HTTP 401 | Invalid token | OK |  | `backend/routes/admin/auth.py:909` |
| ☐ | HTTP 401 | Invalid token | OK |  | `backend/routes/admin/auth.py:916` |
| ☐ | HTTP 401 | Invalid token | OK |  | `backend/routes/admin/auth.py:618` |
| ☐ | HTTP 401 | Invalid token | OK |  | `backend/routes/admin/auth.py:872` |
| ☐ | HTTP 401 | Invalid token | OK |  | `backend/routes/admin/auth.py:896` |
| ☐ | HTTP 401 | Invalid token | OK |  | `backend/routes/admin/auth.py:954` |
| ☐ | HTTP 401 | Invalid token type | OK |  | `backend/routes/admin/auth.py:1113` |
| ☐ | HTTP 401 | MFA enrollment required | OK |  | `backend/routes/admin/auth.py:508` |
| ☐ | HTTP 403 | MFA is mandatory for all staff accounts. Ask a super admin to reset your MFA if you lost your authenticator. | OK |  | `backend/routes/admin/auth.py:1067` |
| ☐ | HTTP 400 | MFA is not enabled on this account | OK |  | `backend/routes/admin/auth.py:1073` |
| ☐ | HTTP 400 | MFA not configured for this account | OK |  | `backend/routes/admin/auth.py:1125` |
| ☐ | HTTP 400 | New password must be at least 12 characters | OK |  | `backend/routes/admin/auth.py:717` |
| ☐ | HTTP 400 | No pending MFA enrollment. Call /mfa/enroll first. | OK |  | `backend/routes/admin/auth.py:997` |
| ☐ | HTTP 401 | Not authenticated | OK |  | `backend/routes/admin/auth.py:594` |
| ☐ | HTTP 401 | Not authenticated | OK |  | `backend/routes/admin/auth.py:851` |
| ☐ | HTTP 401 | Not authenticated | OK |  | `backend/routes/admin/auth.py:934` |
| ☐ | HTTP 404 | Not found | OK |  | `backend/routes/admin/auth.py:1216` |
| ☐ | HTTP 404 | Staff member not found | OK |  | `backend/routes/admin/auth.py:629` |
| ☐ | HTTP 404 | Staff member not found | OK |  | `backend/routes/admin/auth.py:899` |
| ☐ | HTTP 404 | Staff member not found | OK |  | `backend/routes/admin/auth.py:960` |
| ☐ | HTTP 400 | Super admin cannot force-logout here. Rotate ADMIN_PASSWORD in the environment to kill all super-admin sessions. | FLAG | constant: ADMIN_PASSWORD | `backend/routes/admin/auth.py:622` |
| ☐ | HTTP 422 | TOTP code required to change password on an MFA-enrolled account | OK |  | `backend/routes/admin/auth.py:709` |
| ☐ | HTTP 403 | This action requires super admin access. | OK |  | `backend/routes/admin/auth.py:1397` |
| ☐ | HTTP 429 | Too many failed codes — try again later | OK |  | `backend/routes/admin/auth.py:1133` |
| ☐ | HTTP 404 | admin not found | OK |  | `backend/routes/admin/auth.py:1408` |
| ☐ | HTTP 400 | admin001_detail | FLAG | code identifier: admin001_detail; bare machine token, not a sentence | `backend/routes/admin/auth.py:877` |
| ☐ | HTTP 422 | email required | OK |  | `backend/routes/admin/auth.py:1401` |
| ☐ | HTTP 400 | justification must be at least 10 characters | OK |  | `backend/routes/admin/auth.py:1226` |

### `auto-payouts-panel`  (2)

| ✓ | Trigger | Message the user sees | Verdict | Issue | Source |
|---|---|---|---|---|---|
| ☐ | setError | Could not load auto-payout data. | OK |  | `admin-dashboard/src/components/auto-payouts-panel.tsx:137` |
| ☐ | setError | This view needs a backend deploy — the weekly-payout endpoints aren't live on this environment yet. | OK |  | `admin-dashboard/src/components/auto-payouts-panel.tsx:137` |

### `booking_import`  (7)

| ✓ | Trigger | Message the user sees | Verdict | Issue | Source |
|---|---|---|---|---|---|
| ☐ | HTTP 403 | Legacy booking import requires super admin access. | OK |  | `backend/routes/admin/booking_import.py:130` |
| ☐ | HTTP 400 | detail | FLAG | bare machine token, not a sentence | `backend/routes/admin/booking_import.py:160` |
| ☐ | HTTP 413 | {label} CSV exceeds the {MAX_CSV_BYTES // 1000000} MB limit | OK |  | `backend/routes/admin/booking_import.py:100` |
| ☐ | HTTP 422 | {label} CSV has {len(rows)} rows; the limit is {MAX_ROWS} per import | OK |  | `backend/routes/admin/booking_import.py:115` |
| ☐ | HTTP 422 | {label} CSV is empty | OK |  | `backend/routes/admin/booking_import.py:105` |
| ☐ | HTTP 422 | {label} CSV must be UTF-8 encoded | OK |  | `backend/routes/admin/booking_import.py:109` |
| ☐ | HTTP 422 | {label} CSV: {e} | OK |  | `backend/routes/admin/booking_import.py:113` |

### `complaints`  (6)

| ✓ | Trigger | Message the user sees | Verdict | Issue | Source |
|---|---|---|---|---|---|
| ☐ | toast | Complaint created | OK |  | `admin-dashboard/src/app/dashboard/support/_tabs/complaints.tsx:89` |
| ☐ | toast | Complaint marked as ${status} | OK |  | `admin-dashboard/src/app/dashboard/support/_tabs/complaints.tsx:99` |
| ☐ | toast | Description is required | OK |  | `admin-dashboard/src/app/dashboard/support/_tabs/complaints.tsx:85` |
| ☐ | toast | Failed to create complaint | OK |  | `admin-dashboard/src/app/dashboard/support/_tabs/complaints.tsx:92` |
| ☐ | toast | Failed to resolve complaint | OK |  | `admin-dashboard/src/app/dashboard/support/_tabs/complaints.tsx:102` |
| ☐ | toast | Ride ID is required | OK |  | `admin-dashboard/src/app/dashboard/support/_tabs/complaints.tsx:84` |

### `compliance`  (3)

| ✓ | Trigger | Message the user sees | Verdict | Issue | Source |
|---|---|---|---|---|---|
| ☐ | HTTP 403 | Compliance reports require super admin access. | OK |  | `backend/routes/admin/compliance.py:66` |
| ☐ | HTTP 422 | date_from must be on or before date_to | FLAG | code identifier: date_from, date_to | `backend/routes/admin/compliance.py:107` |
| ☐ | HTTP 422 | date_from/date_to must be YYYY-MM-DD | FLAG | code identifier: date_from, date_to | `backend/routes/admin/compliance.py:105` |

### `create-ride-modal`  (4)

| ✓ | Trigger | Message the user sees | Verdict | Issue | Source |
|---|---|---|---|---|---|
| ☐ | setError | Failed to create ride | OK |  | `admin-dashboard/src/app/dashboard/rides/_components/create-ride-modal.tsx:319` |
| ☐ | setError | Failed to estimate fare | OK |  | `admin-dashboard/src/app/dashboard/rides/_components/create-ride-modal.tsx:255` |
| ☐ | setError | Failed to fetch location details: ${err} | OK |  | `admin-dashboard/src/app/dashboard/rides/_components/create-ride-modal.tsx:224` |
| ☐ | setError | Promo code rejected | OK |  | `admin-dashboard/src/app/dashboard/rides/_components/create-ride-modal.tsx:275` |

### `data_transfer_export`  (3)

| ✓ | Trigger | Message the user sees | Verdict | Issue | Source |
|---|---|---|---|---|---|
| ☐ | HTTP 403 | Data Transfer export requires super admin access. | OK |  | `backend/routes/admin/data_transfer_export.py:61` |
| ☐ | HTTP 400 | No entities selected | OK |  | `backend/routes/admin/data_transfer_export.py:328` |
| ☐ | HTTP 422 | {len(body.entities)} entities requested; the limit is {MAX_ENTITIES_PER_EXPORT} per export | OK |  | `backend/routes/admin/data_transfer_export.py:330` |

### `data_transfer_import`  (4)

| ✓ | Trigger | Message the user sees | Verdict | Issue | Source |
|---|---|---|---|---|---|
| ☐ | HTTP 403 | Data Transfer import requires super admin access. | OK |  | `backend/routes/admin/data_transfer_import.py:45` |
| ☐ | HTTP 413 | ZIP exceeds the {MAX_ZIP_BYTES // 1000000} MB limit | OK |  | `backend/routes/admin/data_transfer_import.py:51` |
| ☐ | HTTP 422 | str(e) | OK |  | `backend/routes/admin/data_transfer_import.py:85` |
| ☐ | HTTP 422 | str(e) | OK |  | `backend/routes/admin/data_transfer_import.py:112` |

### `data_transfer_jobs`  (5)

| ✓ | Trigger | Message the user sees | Verdict | Issue | Source |
|---|---|---|---|---|---|
| ☐ | HTTP 403 | Data Transfer jobs require super admin access. | OK |  | `backend/routes/admin/data_transfer_jobs.py:41` |
| ☐ | HTTP 404 | Job not found | OK |  | `backend/routes/admin/data_transfer_jobs.py:93` |
| ☐ | HTTP 404 | Job not found | OK |  | `backend/routes/admin/data_transfer_jobs.py:119` |
| ☐ | HTTP 409 | Job status is {job.get('status')}, not completed | OK |  | `backend/routes/admin/data_transfer_jobs.py:124` |
| ☐ | HTTP 410 | This export has expired and was purged | OK |  | `backend/routes/admin/data_transfer_jobs.py:122` |

### `data_transfer_search`  (1)

| ✓ | Trigger | Message the user sees | Verdict | Issue | Source |
|---|---|---|---|---|---|
| ☐ | HTTP 403 | Data Transfer search requires super admin access. | OK |  | `backend/routes/admin/data_transfer_search.py:42` |

### `dispute_evidence_submission`  (4)

| ✓ | Trigger | Message the user sees | Verdict | Issue | Source |
|---|---|---|---|---|---|
| ☐ | HTTP 404 | Dispute not found | OK |  | `backend/routes/admin/dispute_evidence_submission.py:99` |
| ☐ | HTTP 409 | Evidence submission already in progress or completed | OK |  | `backend/routes/admin/dispute_evidence_submission.py:115` |
| ☐ | HTTP 409 | Evidence was already submitted for this dispute | OK |  | `backend/routes/admin/dispute_evidence_submission.py:103` |
| ☐ | HTTP 400 | Set confirm=true to submit evidence to Stripe -- this cannot be un-submitted. | OK |  | `backend/routes/admin/dispute_evidence_submission.py:76` |

### `dispute_pack_download`  (1)

| ✓ | Trigger | Message the user sees | Verdict | Issue | Source |
|---|---|---|---|---|---|
| ☐ | HTTP 404 | Ride not found | OK |  | `backend/routes/admin/dispute_pack_download.py:77` |

### `document-reviewer`  (13)

| ✓ | Trigger | Message the user sees | Verdict | Issue | Source |
|---|---|---|---|---|---|
| ☐ | toast | Approval failed | OK |  | `admin-dashboard/src/app/dashboard/drivers/_components/document-reviewer.tsx:192` |
| ☐ | toast | Could not load documents | OK |  | `admin-dashboard/src/app/dashboard/drivers/_components/document-reviewer.tsx:154` |
| ☐ | toast | Document approved | OK |  | `admin-dashboard/src/app/dashboard/drivers/_components/document-reviewer.tsx:190` |
| ☐ | toast | Document rejected | OK |  | `admin-dashboard/src/app/dashboard/drivers/_components/document-reviewer.tsx:180` |
| ☐ | toast | Download failed | OK |  | `admin-dashboard/src/app/dashboard/drivers/_components/document-reviewer.tsx:123` |
| ☐ | toast | Driver was notified. | OK |  | `admin-dashboard/src/app/dashboard/drivers/_components/document-reviewer.tsx:180` |
| ☐ | toast | Expiry date required | OK |  | `admin-dashboard/src/app/dashboard/drivers/_components/document-reviewer.tsx:164` |
| ☐ | toast | Pick a template or write a reason. | OK |  | `admin-dashboard/src/app/dashboard/drivers/_components/document-reviewer.tsx:171` |
| ☐ | toast | Reason required | OK |  | `admin-dashboard/src/app/dashboard/drivers/_components/document-reviewer.tsx:171` |
| ☐ | toast | Rejected without notification. | OK |  | `admin-dashboard/src/app/dashboard/drivers/_components/document-reviewer.tsx:180` |
| ☐ | toast | Rejection failed | OK |  | `admin-dashboard/src/app/dashboard/drivers/_components/document-reviewer.tsx:182` |
| ☐ | toast | This document needs an expiry date before approval. | OK |  | `admin-dashboard/src/app/dashboard/drivers/_components/document-reviewer.tsx:164` |
| ☐ | toast | Unknown error | OK |  | `admin-dashboard/src/app/dashboard/drivers/_components/document-reviewer.tsx:123` |

### `document-upload-dialog`  (5)

| ✓ | Trigger | Message the user sees | Verdict | Issue | Source |
|---|---|---|---|---|---|
| ☐ | toast | ${ok} document${ok === 1 ? "" : "s"} uploaded for ${driverName \|\| "this driver"}. | OK |  | `admin-dashboard/src/app/dashboard/drivers/_components/document-upload-dialog.tsx:176` |
| ☐ | toast | ${ok} succeeded, ${failures.length} failed. ${failures[0]} | OK |  | `admin-dashboard/src/app/dashboard/drivers/_components/document-upload-dialog.tsx:184` |
| ☐ | toast | Documents uploaded | OK |  | `admin-dashboard/src/app/dashboard/drivers/_components/document-upload-dialog.tsx:176` |
| ☐ | toast | Some documents failed | OK |  | `admin-dashboard/src/app/dashboard/drivers/_components/document-upload-dialog.tsx:184` |
| ☐ | toast | Upload failed | OK |  | `admin-dashboard/src/app/dashboard/drivers/_components/document-upload-dialog.tsx:184` |

### `documents`  (11)

| ✓ | Trigger | Message the user sees | Verdict | Issue | Source |
|---|---|---|---|---|---|
| ☐ | HTTP 404 | Document has no resolvable storage key | OK |  | `backend/routes/admin/documents.py:92` |
| ☐ | HTTP 404 | Document not found | OK |  | `backend/routes/admin/documents.py:86` |
| ☐ | HTTP 404 | Document not found | OK |  | `backend/routes/admin/documents.py:327` |
| ☐ | HTTP 404 | Driver not found | OK |  | `backend/routes/admin/documents.py:687` |
| ☐ | HTTP 422 | Driver's licence number/class must be on file before approval — use the driver-license-backfill queue or edit the driver record first. | OK |  | `backend/routes/admin/documents.py:371` |
| ☐ | HTTP 400 | Invalid status | OK |  | `backend/routes/admin/documents.py:320` |
| ☐ | HTTP 404 | Requirement not found for this driver's service area | OK |  | `backend/routes/admin/documents.py:715` |
| ☐ | HTTP 400 | expiry_date is required before approving this document | FLAG | code identifier: expiry_date | `backend/routes/admin/documents.py:725` |
| ☐ | HTTP 400 | expiry_date must be an ISO date | FLAG | code identifier: expiry_date | `backend/routes/admin/documents.py:722` |
| ☐ | HTTP 400 | side must be 'front' or 'back' | OK |  | `backend/routes/admin/documents.py:683` |
| ☐ | HTTP 400 | status must be 'pending' or 'approved' | OK |  | `backend/routes/admin/documents.py:681` |

### `driver-action-bar`  (3)

| ✓ | Trigger | Message the user sees | Verdict | Issue | Source |
|---|---|---|---|---|---|
| ☐ | toast | Action failed | OK |  | `admin-dashboard/src/app/dashboard/drivers/_components/driver-action-bar.tsx:213` |
| ☐ | toast | Action recorded | OK |  | `admin-dashboard/src/app/dashboard/drivers/_components/driver-action-bar.tsx:206` |
| ☐ | toast | Ref: ${result.audit_log_id} | OK |  | `admin-dashboard/src/app/dashboard/drivers/_components/driver-action-bar.tsx:206` |

### `driver-detail-shared`  (1)

| ✓ | Trigger | Message the user sees | Verdict | Issue | Source |
|---|---|---|---|---|---|
| ☐ | toast | ${label} copied | OK |  | `admin-dashboard/src/app/dashboard/drivers/_components/driver-detail-shared.tsx:106` |

### `driver-detail-sheet`  (16)

| ✓ | Trigger | Message the user sees | Verdict | Issue | Source |
|---|---|---|---|---|---|
| ☐ | toast | Auto-hides in 30 seconds. Reveal logged. | OK |  | `admin-dashboard/src/app/dashboard/drivers/_components/driver-detail-sheet.tsx:580` |
| ☐ | toast | Not synced | OK |  | `admin-dashboard/src/app/dashboard/drivers/_components/driver-detail-sheet.tsx:542` |
| ☐ | toast | Not synced | OK |  | `admin-dashboard/src/app/dashboard/drivers/_components/driver-detail-sheet.tsx:562` |
| ☐ | toast | Payout sent back to Stripe for processing. | OK |  | `admin-dashboard/src/app/dashboard/drivers/_components/driver-detail-sheet.tsx:527` |
| ☐ | toast | Payout sync failed | OK |  | `admin-dashboard/src/app/dashboard/drivers/_components/driver-detail-sheet.tsx:568` |
| ☐ | toast | Payouts synced from Stripe | OK |  | `admin-dashboard/src/app/dashboard/drivers/_components/driver-detail-sheet.tsx:562` |
| ☐ | toast | Refresh failed | OK |  | `admin-dashboard/src/app/dashboard/drivers/_components/driver-detail-sheet.tsx:548` |
| ☐ | toast | Retry failed | OK |  | `admin-dashboard/src/app/dashboard/drivers/_components/driver-detail-sheet.tsx:531` |
| ☐ | toast | Retry queued | OK |  | `admin-dashboard/src/app/dashboard/drivers/_components/driver-detail-sheet.tsx:527` |
| ☐ | toast | Reveal failed | OK |  | `admin-dashboard/src/app/dashboard/drivers/_components/driver-detail-sheet.tsx:582` |
| ☐ | toast | SIN revealed | OK |  | `admin-dashboard/src/app/dashboard/drivers/_components/driver-detail-sheet.tsx:580` |
| ☐ | toast | Synced from Stripe | OK |  | `admin-dashboard/src/app/dashboard/drivers/_components/driver-detail-sheet.tsx:542` |
| ☐ | toast | Unknown error | OK |  | `admin-dashboard/src/app/dashboard/drivers/_components/driver-detail-sheet.tsx:531` |
| ☐ | toast | Unknown error | OK |  | `admin-dashboard/src/app/dashboard/drivers/_components/driver-detail-sheet.tsx:548` |
| ☐ | toast | Unknown error | OK |  | `admin-dashboard/src/app/dashboard/drivers/_components/driver-detail-sheet.tsx:568` |
| ☐ | toast | Unknown error | OK |  | `admin-dashboard/src/app/dashboard/drivers/_components/driver-detail-sheet.tsx:582` |

### `driver-documents-helpers`  (2)

| ✓ | Trigger | Message the user sees | Verdict | Issue | Source |
|---|---|---|---|---|---|
| ☐ | toast | Download failed | OK |  | `admin-dashboard/src/app/dashboard/drivers/_components/driver-documents-helpers.tsx:210` |
| ☐ | toast | Unknown error | OK |  | `admin-dashboard/src/app/dashboard/drivers/_components/driver-documents-helpers.tsx:210` |

### `driver-notes`  (1)

| ✓ | Trigger | Message the user sees | Verdict | Issue | Source |
|---|---|---|---|---|---|
| ☐ | toast | Failed to add note | OK |  | `admin-dashboard/src/app/dashboard/drivers/_components/driver-notes.tsx:88` |

### `driver-offers-panel`  (1)

| ✓ | Trigger | Message the user sees | Verdict | Issue | Source |
|---|---|---|---|---|---|
| ☐ | setError | Failed to load offer analytics. Please try again. | OK |  | `admin-dashboard/src/components/analytics/driver-offers-panel.tsx:87` |

### `driver-panel`  (2)

| ✓ | Trigger | Message the user sees | Verdict | Issue | Source |
|---|---|---|---|---|---|
| ☐ | toast | Open the full profile for ${driver.name} to submit a formal flag. | OK |  | `admin-dashboard/src/app/dashboard/monitoring/driver-panel.tsx:186` |
| ☐ | toast | Use driver profile to flag | OK |  | `admin-dashboard/src/app/dashboard/monitoring/driver-panel.tsx:186` |

### `driver_appeals`  (4)

| ✓ | Trigger | Message the user sees | Verdict | Issue | Source |
|---|---|---|---|---|---|
| ☐ | HTTP 409 | Appeal is already {appeal.get('status')} | OK |  | `backend/routes/admin/driver_appeals.py:84` |
| ☐ | HTTP 404 | Appeal not found | OK |  | `backend/routes/admin/driver_appeals.py:82` |
| ☐ | HTTP 400 | decision must be 'approved' or 'denied' | OK |  | `backend/routes/admin/driver_appeals.py:78` |
| ☐ | HTTP 400 | str(e) | OK |  | `backend/routes/admin/driver_appeals.py:63` |

### `driver_distance`  (3)

| ✓ | Trigger | Message the user sees | Verdict | Issue | Source |
|---|---|---|---|---|---|
| ☐ | HTTP 400 | range is capped at {MAX_RANGE_DAYS} days | OK |  | `backend/routes/admin/driver_distance.py:84` |
| ☐ | HTTP 400 | start must be on or before end | OK |  | `backend/routes/admin/driver_distance.py:82` |
| ☐ | HTTP 400 | {param} must be YYYY-MM-DD | OK |  | `backend/routes/admin/driver_distance.py:59` |

### `driver_dormancy`  (1)

| ✓ | Trigger | Message the user sees | Verdict | Issue | Source |
|---|---|---|---|---|---|
| ☐ | HTTP 403 | Driver dormancy flagging requires super admin access. | OK |  | `backend/routes/admin/driver_dormancy.py:57` |

### `driver_import`  (6)

| ✓ | Trigger | Message the user sees | Verdict | Issue | Source |
|---|---|---|---|---|---|
| ☐ | HTTP 413 | CSV exceeds the {MAX_CSV_BYTES // 1000} KB limit | OK |  | `backend/routes/admin/driver_import.py:79` |
| ☐ | HTTP 422 | CSV has {len(rows)} rows; the limit is {MAX_ROWS} per import | OK |  | `backend/routes/admin/driver_import.py:90` |
| ☐ | HTTP 422 | CSV must be UTF-8 encoded | OK |  | `backend/routes/admin/driver_import.py:84` |
| ☐ | HTTP 400 | Validate this CSV before committing (or re-validate — the file or batch changed): {e} | OK |  | `backend/routes/admin/driver_import.py:179` |
| ☐ | HTTP 422 | str(e) | OK |  | `backend/routes/admin/driver_import.py:88` |
| ☐ | HTTP 400 | str(e) | OK |  | `backend/routes/admin/driver_import.py:111` |

### `driver_statements`  (8)

| ✓ | Trigger | Message the user sees | Verdict | Issue | Source |
|---|---|---|---|---|---|
| ☐ | HTTP 400 | Driver has no email address on file | OK |  | `backend/routes/admin/driver_statements.py:216` |
| ☐ | HTTP 404 | Driver not found | OK |  | `backend/routes/admin/driver_statements.py:74` |
| ☐ | HTTP 422 | Provide both start and end for a date range | OK |  | `backend/routes/admin/driver_statements.py:100` |
| ☐ | HTTP 422 | Provide period_type+period_start or start+end dates | FLAG | code identifier: period_start, period_type | `backend/routes/admin/driver_statements.py:113` |
| ☐ | HTTP 403 | Recomputing statement totals requires super admin access. | OK |  | `backend/routes/admin/driver_statements.py:292` |
| ☐ | HTTP 422 | period_type must be weekly or monthly | FLAG | code identifier: period_type | `backend/routes/admin/driver_statements.py:106` |
| ☐ | HTTP 422 | str(e) | OK |  | `backend/routes/admin/driver_statements.py:112` |
| ☐ | HTTP 422 | {field} must be YYYY-MM-DD | OK |  | `backend/routes/admin/driver_statements.py:68` |

### `drivers`  (42)

| ✓ | Trigger | Message the user sees | Verdict | Issue | Source |
|---|---|---|---|---|---|
| ☐ | HTTP 403 | Bulk KYC refresh requires super admin access. | OK |  | `backend/routes/admin/drivers.py:3875` |
| ☐ | HTTP 400 | Driver has no Stripe Connect account on file. They must complete payout setup first. | OK |  | `backend/routes/admin/drivers.py:3490` |
| ☐ | HTTP 400 | Driver has no linked user account | OK |  | `backend/routes/admin/drivers.py:1625` |
| ☐ | HTTP 422 | Driver has no linked user account | OK |  | `backend/routes/admin/drivers.py:2025` |
| ☐ | HTTP 422 | Driver has no linked user account | OK |  | `backend/routes/admin/drivers.py:2076` |
| ☐ | HTTP 409 | Driver has no linked user account; cannot update email/gender. | OK |  | `backend/routes/admin/drivers.py:1872` |
| ☐ | HTTP 404 | Driver not found | OK |  | `backend/routes/admin/drivers.py:1622` |
| ☐ | HTTP 404 | Driver not found | OK |  | `backend/routes/admin/drivers.py:2114` |
| ☐ | HTTP 404 | Driver not found | OK |  | `backend/routes/admin/drivers.py:2250` |
| ☐ | HTTP 404 | Driver not found | OK |  | `backend/routes/admin/drivers.py:2661` |
| ☐ | HTTP 404 | Driver not found | OK |  | `backend/routes/admin/drivers.py:2680` |
| ☐ | HTTP 404 | Driver not found | OK |  | `backend/routes/admin/drivers.py:3130` |
| ☐ | HTTP 404 | Driver not found | OK |  | `backend/routes/admin/drivers.py:3399` |
| ☐ | HTTP 404 | Driver not found | OK |  | `backend/routes/admin/drivers.py:3481` |
| ☐ | HTTP 404 | Driver not found | OK |  | `backend/routes/admin/drivers.py:3959` |
| ☐ | HTTP 404 | Driver not found | OK |  | `backend/routes/admin/drivers.py:4042` |
| ☐ | HTTP 404 | Driver not found | OK |  | `backend/routes/admin/drivers.py:4397` |
| ☐ | HTTP 409 | Driver record changed or was removed mid-update — the SIN was NOT changed. Reload and retry. | OK |  | `backend/routes/admin/drivers.py:4090` |
| ☐ | HTTP 404 | Driver {driver_id} not found | OK |  | `backend/routes/admin/drivers.py:1820` |
| ☐ | HTTP 404 | Driver {driver_id} not found | OK |  | `backend/routes/admin/drivers.py:2022` |
| ☐ | HTTP 404 | Driver {driver_id} not found | OK |  | `backend/routes/admin/drivers.py:2073` |
| ☐ | HTTP 404 | Driver {driver_id} not found | OK |  | `backend/routes/admin/drivers.py:1973` |
| ☐ | HTTP 404 | Driver {driver_id} not found | OK |  | `backend/routes/admin/drivers.py:4322` |
| ☐ | HTTP 400 | File must be an image (JPEG, PNG, WebP, or GIF) | OK |  | `backend/routes/admin/drivers.py:2069` |
| ☐ | HTTP 400 | Image must be smaller than 5MB | OK |  | `backend/routes/admin/drivers.py:2082` |
| ☐ | HTTP 400 | Invalid status. Must be one of: {', '.join(valid)} | OK |  | `backend/routes/admin/drivers.py:2243` |
| ☐ | HTTP 400 | Invalid work_authorization_status. Must be one of: {', '.join(sorted(WORK_AUTHORIZATION_CHOICES))} | FLAG | code identifier: work_authorization_status | `backend/routes/admin/drivers.py:1854` |
| ☐ | HTTP 400 | No SIN on file. The driver supplies it in the app under Payouts; it cannot be recovered from Stripe. | OK |  | `backend/routes/admin/drivers.py:3964` |
| ☐ | HTTP 404 | No failed claim for this referee | OK |  | `backend/routes/admin/drivers.py:3063` |
| ☐ | HTTP 400 | No valid fields to update | OK |  | `backend/routes/admin/drivers.py:1816` |
| ☐ | HTTP 400 | Note cannot be empty | OK |  | `backend/routes/admin/drivers.py:2328` |
| ☐ | HTTP 400 | Reason is required when banning | OK |  | `backend/routes/admin/drivers.py:2154` |
| ☐ | HTTP 400 | Reason is required when rejecting | OK |  | `backend/routes/admin/drivers.py:2134` |
| ☐ | HTTP 400 | Reason is required when suspending | OK |  | `backend/routes/admin/drivers.py:2144` |
| ☐ | HTTP 403 | Revealing a SIN requires super admin access. | OK |  | `backend/routes/admin/drivers.py:3955` |
| ☐ | HTTP 403 | Stripe payout refresh requires super admin access. | OK |  | `backend/routes/admin/drivers.py:3477` |
| ☐ | HTTP 403 | Stripe payout refresh requires super admin access. | OK |  | `backend/routes/admin/drivers.py:3735` |
| ☐ | HTTP 400 | Unknown action: {req.action} | OK |  | `backend/routes/admin/drivers.py:2179` |
| ☐ | HTTP 403 | Updating a SIN requires super admin access. | OK |  | `backend/routes/admin/drivers.py:4038` |
| ☐ | HTTP 400 | date must be YYYY-MM-DD | OK |  | `backend/routes/admin/drivers.py:4237` |
| ☐ | HTTP 400 | missing_license cannot be combined with search | FLAG | code identifier: missing_license | `backend/routes/admin/drivers.py:666` |
| ☐ | HTTP 422 | str(exc) | OK |  | `backend/routes/admin/drivers.py:4054` |

### `efficiency-panel`  (1)

| ✓ | Trigger | Message the user sees | Verdict | Issue | Source |
|---|---|---|---|---|---|
| ☐ | setError | Could not load efficiency metrics. | OK |  | `admin-dashboard/src/components/analytics/efficiency-panel.tsx:62` |

### `export_approvals`  (7)

| ✓ | Trigger | Message the user sees | Verdict | Issue | Source |
|---|---|---|---|---|---|
| ☐ | HTTP 404 | Export approval request not found | OK |  | `backend/routes/admin/export_approvals.py:67` |
| ☐ | HTTP 404 | Export approval request not found | OK |  | `backend/routes/admin/export_approvals.py:95` |
| ☐ | HTTP 403 | Export approvals require super admin access. | OK |  | `backend/routes/admin/export_approvals.py:40` |
| ☐ | HTTP 403 | You cannot approve your own export request | OK |  | `backend/routes/admin/export_approvals.py:71` |
| ☐ | HTTP 403 | You cannot deny your own export request | OK |  | `backend/routes/admin/export_approvals.py:99` |
| ☐ | HTTP 409 | str(e) | OK |  | `backend/routes/admin/export_approvals.py:69` |
| ☐ | HTTP 409 | str(e) | OK |  | `backend/routes/admin/export_approvals.py:97` |

### `financial-panel`  (1)

| ✓ | Trigger | Message the user sees | Verdict | Issue | Source |
|---|---|---|---|---|---|
| ☐ | setError | Could not load financial metrics. | OK |  | `admin-dashboard/src/components/analytics/financial-panel.tsx:43` |

### `flags`  (3)

| ✓ | Trigger | Message the user sees | Verdict | Issue | Source |
|---|---|---|---|---|---|
| ☐ | toast | Failed to create flag | OK |  | `admin-dashboard/src/app/dashboard/support/_tabs/flags.tsx:82` |
| ☐ | toast | Flag created | OK |  | `admin-dashboard/src/app/dashboard/support/_tabs/flags.tsx:79` |
| ☐ | toast | Missing ride ID | OK |  | `admin-dashboard/src/app/dashboard/support/_tabs/flags.tsx:75` |

### `incentives`  (5)

| ✓ | Trigger | Message the user sees | Verdict | Issue | Source |
|---|---|---|---|---|---|
| ☐ | HTTP 404 | Incentive not found | OK |  | `backend/routes/admin/incentives.py:114` |
| ☐ | HTTP 404 | Incentive not found | OK |  | `backend/routes/admin/incentives.py:180` |
| ☐ | HTTP 404 | Incentive not found | OK |  | `backend/routes/admin/incentives.py:208` |
| ☐ | HTTP 404 | Incentive not found | OK |  | `backend/routes/admin/incentives.py:240` |
| ☐ | HTTP 400 | No fields to update | OK |  | `backend/routes/admin/incentives.py:171` |

### `layout`  (2)

| ✓ | Trigger | Message the user sees | Verdict | Issue | Source |
|---|---|---|---|---|---|
| ☐ | setError | Failed to load | OK |  | `admin-dashboard/src/app/company-portal/[id]/layout.tsx:79` |
| ☐ | setError | You are not a member of this company. | OK |  | `admin-dashboard/src/app/company-portal/[id]/layout.tsx:75` |

### `legacy_driver_import`  (7)

| ✓ | Trigger | Message the user sees | Verdict | Issue | Source |
|---|---|---|---|---|---|
| ☐ | HTTP 413 | CSV exceeds the {MAX_CSV_BYTES // 1000000} MB limit | OK |  | `backend/routes/admin/legacy_driver_import.py:91` |
| ☐ | HTTP 422 | CSV has {len(rows)} rows; the limit is {MAX_ROWS} per import | OK |  | `backend/routes/admin/legacy_driver_import.py:102` |
| ☐ | HTTP 422 | CSV must be UTF-8 encoded | OK |  | `backend/routes/admin/legacy_driver_import.py:96` |
| ☐ | HTTP 400 | Validate this CSV before committing (or re-validate — the file or batch changed): {e} | OK |  | `backend/routes/admin/legacy_driver_import.py:201` |
| ☐ | HTTP 422 | str(e) | OK |  | `backend/routes/admin/legacy_driver_import.py:100` |
| ☐ | HTTP 400 | str(e) | OK |  | `backend/routes/admin/legacy_driver_import.py:130` |
| ☐ | HTTP 400 | str(e) | OK |  | `backend/routes/admin/legacy_driver_import.py:279` |

### `legacy_duration_estimated_backfill`  (1)

| ✓ | Trigger | Message the user sees | Verdict | Issue | Source |
|---|---|---|---|---|---|
| ☐ | HTTP 403 | Duration-estimated marker backfill requires super admin access. | OK |  | `backend/routes/admin/legacy_duration_estimated_backfill.py:69` |

### `legacy_id_crosswalk`  (1)

| ✓ | Trigger | Message the user sees | Verdict | Issue | Source |
|---|---|---|---|---|---|
| ☐ | HTTP 403 | Legacy ID crosswalk backfill requires super admin access. | OK |  | `backend/routes/admin/legacy_id_crosswalk.py:55` |

### `legacy_saved_address_backfill`  (5)

| ✓ | Trigger | Message the user sees | Verdict | Issue | Source |
|---|---|---|---|---|---|
| ☐ | HTTP 400 | Validate these CSVs before committing (or re-validate — a file or batch changed): {e} | OK |  | `backend/routes/admin/legacy_saved_address_backfill.py:174` |
| ☐ | HTTP 413 | {label} exceeds the {MAX_CSV_BYTES // 1000000} MB limit | OK |  | `backend/routes/admin/legacy_saved_address_backfill.py:88` |
| ☐ | HTTP 422 | {label} has {len(rows)} rows; the limit is {max_rows} | OK |  | `backend/routes/admin/legacy_saved_address_backfill.py:98` |
| ☐ | HTTP 422 | {label} must be UTF-8 encoded | OK |  | `backend/routes/admin/legacy_saved_address_backfill.py:92` |
| ☐ | HTTP 422 | {label}: {e} | OK |  | `backend/routes/admin/legacy_saved_address_backfill.py:96` |

### `legacy_sin_dob_backfill`  (6)

| ✓ | Trigger | Message the user sees | Verdict | Issue | Source |
|---|---|---|---|---|---|
| ☐ | HTTP 413 | CSV exceeds the {MAX_CSV_BYTES // 1000000} MB limit | OK |  | `backend/routes/admin/legacy_sin_dob_backfill.py:103` |
| ☐ | HTTP 422 | CSV has {len(rows)} rows; the limit is {MAX_ROWS} per import | OK |  | `backend/routes/admin/legacy_sin_dob_backfill.py:113` |
| ☐ | HTTP 422 | CSV must be UTF-8 encoded | OK |  | `backend/routes/admin/legacy_sin_dob_backfill.py:107` |
| ☐ | HTTP 403 | Legacy SIN/DOB backfill requires super admin access. | OK |  | `backend/routes/admin/legacy_sin_dob_backfill.py:85` |
| ☐ | HTTP 400 | Validate these CSVs before committing (or re-validate — a file or batch changed): {e} | OK |  | `backend/routes/admin/legacy_sin_dob_backfill.py:207` |
| ☐ | HTTP 422 | str(e) | OK |  | `backend/routes/admin/legacy_sin_dob_backfill.py:111` |

### `legacy_vehicle_history_backfill`  (5)

| ✓ | Trigger | Message the user sees | Verdict | Issue | Source |
|---|---|---|---|---|---|
| ☐ | HTTP 400 | Validate these CSVs before committing (or re-validate — a file or batch changed): {e} | OK |  | `backend/routes/admin/legacy_vehicle_history_backfill.py:203` |
| ☐ | HTTP 413 | {label} exceeds the {MAX_CSV_BYTES // 1000000} MB limit | OK |  | `backend/routes/admin/legacy_vehicle_history_backfill.py:98` |
| ☐ | HTTP 422 | {label} has {len(rows)} rows; the limit is {max_rows} | OK |  | `backend/routes/admin/legacy_vehicle_history_backfill.py:108` |
| ☐ | HTTP 422 | {label} must be UTF-8 encoded | OK |  | `backend/routes/admin/legacy_vehicle_history_backfill.py:102` |
| ☐ | HTTP 422 | {label}: {e} | OK |  | `backend/routes/admin/legacy_vehicle_history_backfill.py:106` |

### `legal-documents`  (2)

| ✓ | Trigger | Message the user sees | Verdict | Issue | Source |
|---|---|---|---|---|---|
| ☐ | toast | Failed to save document | OK |  | `admin-dashboard/src/app/dashboard/support/_tabs/legal-documents.tsx:134` |
| ☐ | toast | Legal document saved | OK |  | `admin-dashboard/src/app/dashboard/support/_tabs/legal-documents.tsx:131` |

### `legal_documents`  (2)

| ✓ | Trigger | Message the user sees | Verdict | Issue | Source |
|---|---|---|---|---|---|
| ☐ | HTTP 400 | audience must be 'rider' or 'driver' | OK |  | `backend/routes/admin/legal_documents.py:63` |
| ☐ | HTTP 400 | type must be one of: {', '.join(sorted(ALLOWED_TYPES))} | OK |  | `backend/routes/admin/legal_documents.py:65` |

### `lost-and-found`  (14)

| ✓ | Trigger | Message the user sees | Verdict | Issue | Source |
|---|---|---|---|---|---|
| ☐ | toast | Enter ride ID and item description. | OK |  | `admin-dashboard/src/app/dashboard/support/_tabs/lost-and-found.tsx:82` |
| ☐ | toast | Failed to delete item | OK |  | `admin-dashboard/src/app/dashboard/support/_tabs/lost-and-found.tsx:179` |
| ☐ | toast | Failed to report item | OK |  | `admin-dashboard/src/app/dashboard/support/_tabs/lost-and-found.tsx:89` |
| ☐ | toast | Failed to update item | OK |  | `admin-dashboard/src/app/dashboard/support/_tabs/lost-and-found.tsx:100` |
| ☐ | toast | Failed to update status | OK |  | `admin-dashboard/src/app/dashboard/support/_tabs/lost-and-found.tsx:108` |
| ☐ | toast | Lost item deleted | OK |  | `admin-dashboard/src/app/dashboard/support/_tabs/lost-and-found.tsx:179` |
| ☐ | toast | Lost item reported | OK |  | `admin-dashboard/src/app/dashboard/support/_tabs/lost-and-found.tsx:86` |
| ☐ | toast | Lost item updated | OK |  | `admin-dashboard/src/app/dashboard/support/_tabs/lost-and-found.tsx:97` |
| ☐ | toast | Marked as ${status} | OK |  | `admin-dashboard/src/app/dashboard/support/_tabs/lost-and-found.tsx:106` |
| ☐ | toast | Missing fields | OK |  | `admin-dashboard/src/app/dashboard/support/_tabs/lost-and-found.tsx:82` |
| ☐ | toast | Please try again | OK |  | `admin-dashboard/src/app/dashboard/support/_tabs/lost-and-found.tsx:89` |
| ☐ | toast | Please try again | OK |  | `admin-dashboard/src/app/dashboard/support/_tabs/lost-and-found.tsx:100` |
| ☐ | toast | Please try again | OK |  | `admin-dashboard/src/app/dashboard/support/_tabs/lost-and-found.tsx:108` |
| ☐ | toast | Please try again | OK |  | `admin-dashboard/src/app/dashboard/support/_tabs/lost-and-found.tsx:179` |

### `maintenance`  (1)

| ✓ | Trigger | Message the user sees | Verdict | Issue | Source |
|---|---|---|---|---|---|
| ☐ | HTTP 422 | target_date must be a completed America/Regina day (yesterday or earlier) | FLAG | code identifier: target_date | `backend/routes/admin/maintenance.py:150` |

### `marketplace-overview-panel`  (1)

| ✓ | Trigger | Message the user sees | Verdict | Issue | Source |
|---|---|---|---|---|---|
| ☐ | setError | Could not load marketplace metrics. | OK |  | `admin-dashboard/src/components/analytics/marketplace-overview-panel.tsx:41` |

### `messaging`  (3)

| ✓ | Trigger | Message the user sees | Verdict | Issue | Source |
|---|---|---|---|---|---|
| ☐ | HTTP 400 | Cannot delete a sent message | OK |  | `backend/routes/admin/messaging.py:553` |
| ☐ | HTTP 404 | Message not found | OK |  | `backend/routes/admin/messaging.py:550` |
| ☐ | HTTP 404 | Suppression not found | OK |  | `backend/routes/admin/messaging.py:531` |

### `mfa-enroll-dialog`  (4)

| ✓ | Trigger | Message the user sees | Verdict | Issue | Source |
|---|---|---|---|---|---|
| ☐ | setError | Failed to start enrollment | OK |  | `admin-dashboard/src/components/mfa-enroll-dialog.tsx:53` |
| ☐ | setError | Invalid code. Please try again. | OK |  | `admin-dashboard/src/components/mfa-enroll-dialog.tsx:68` |
| ☐ | toast | MFA enabled | OK |  | `admin-dashboard/src/components/mfa-enroll-dialog.tsx:75` |
| ☐ | toast | Two-factor authentication is now active on your account. | OK |  | `admin-dashboard/src/components/mfa-enroll-dialog.tsx:75` |

### `migration_data_quality`  (1)

| ✓ | Trigger | Message the user sees | Verdict | Issue | Source |
|---|---|---|---|---|---|
| ☐ | HTTP 403 | Migration data-quality scan requires super admin access. | OK |  | `backend/routes/admin/migration_data_quality.py:55` |

### `migration_driver_repair`  (1)

| ✓ | Trigger | Message the user sees | Verdict | Issue | Source |
|---|---|---|---|---|---|
| ☐ | HTTP 403 | Driver-repair pass requires super admin access. | OK |  | `backend/routes/admin/migration_driver_repair.py:53` |

### `migration_status`  (1)

| ✓ | Trigger | Message the user sees | Verdict | Issue | Source |
|---|---|---|---|---|---|
| ☐ | HTTP 403 | Migration status requires super admin access. | OK |  | `backend/routes/admin/migration_status.py:38` |

### `monitoring`  (2)

| ✓ | Trigger | Message the user sees | Verdict | Issue | Source |
|---|---|---|---|---|---|
| ☐ | HTTP 400 | Confirmation required: set `confirm` to 'FLUSH' to proceed. | OK |  | `backend/routes/admin/monitoring.py:566` |
| ☐ | HTTP 403 | Prefix '{prefix}' is not in the flushable allowlist. Allowed: {sorted(_FLUSHABLE_PREFIXES)} | OK |  | `backend/routes/admin/monitoring.py:573` |

### `page`  (452)

| ✓ | Trigger | Message the user sees | Verdict | Issue | Source |
|---|---|---|---|---|---|
| ☐ | toast | ${stranded} unreachable on the current key. | OK |  | `admin-dashboard/src/app/dashboard/users/page.tsx:266` |
| ☐ | toast | · ${applied.skipped.length} skipped | OK |  | `admin-dashboard/src/app/dashboard/drivers/page.tsx:716` |
| ☐ | toast | · ${totals.failed} failed | OK |  | `admin-dashboard/src/app/dashboard/users/page.tsx:312` |
| ☐ | toast | · ${totals.stranded} unreachable | OK |  | `admin-dashboard/src/app/dashboard/users/page.tsx:312` |
| ☐ | toast | · ${totals.throttled} rate-limited by Stripe | OK |  | `admin-dashboard/src/app/dashboard/users/page.tsx:312` |
| ☐ | toast | · more remain, run again | OK |  | `admin-dashboard/src/app/dashboard/drivers/page.tsx:716` |
| ☐ | toast | · stopped after ${batches} batches | OK |  | `admin-dashboard/src/app/dashboard/users/page.tsx:312` |
| ☐ | toast | — run again to finish (safe to repeat) | OK |  | `admin-dashboard/src/app/dashboard/users/page.tsx:312` |
| ☐ | toast | ${applied.corrected} corrected, ${applied.unchanged} already correct | OK |  | `admin-dashboard/src/app/dashboard/drivers/page.tsx:716` |
| ☐ | toast | ${bits}. Upload it below, validate, then commit. | OK |  | `admin-dashboard/src/app/dashboard/bulk-operations/page.tsx:427` |
| ☐ | toast | ${created + updated} record(s) processed. | OK |  | `admin-dashboard/src/app/dashboard/bulk-operations/page.tsx:1496` |
| ☐ | toast | ${deleteTarget.email} removed. | OK |  | `admin-dashboard/src/app/dashboard/staff/page.tsx:220` |
| ☐ | toast | ${dismissTarget} marked as processed. | OK |  | `admin-dashboard/src/app/dashboard/stripe-events/page.tsx:182` |
| ☐ | toast | ${driver.name \|\| driver.first_name \|\| driver.id} updated — removed from this queue. | OK |  | `admin-dashboard/src/app/dashboard/driver-license-backfill/page.tsx:97` |
| ☐ | toast | ${it.name} — ${it.doc_label} | OK |  | `admin-dashboard/src/app/dashboard/drivers/expiring/page.tsx:86` |
| ☐ | toast | ${label} updated | OK |  | `admin-dashboard/src/app/dashboard/support-tickets/tickets/[id]/page.tsx:260` |
| ☐ | toast | ${mapped} record(s) mapped. | OK |  | `admin-dashboard/src/app/dashboard/bulk-operations/page.tsx:506` |
| ☐ | toast | ${mfaResetTarget.email} was signed out everywhere and can now log in with email + password, then re-enroll from Settings. | OK |  | `admin-dashboard/src/app/dashboard/staff/page.tsx:233` |
| ☐ | toast | ${preview.scanned} Stripe customer${preview.scanned === 1 ? "" : "s"} checked — all already carry the rider's email. | OK |  | `admin-dashboard/src/app/dashboard/users/page.tsx:266` |
| ☐ | toast | ${preview.scanned} statement${preview.scanned === 1 ? "" : "s"} checked — all totals already match a live recompute. | OK |  | `admin-dashboard/src/app/dashboard/drivers/page.tsx:696` |
| ☐ | toast | ${r.upserted ?? 0} tickets updated | OK |  | `admin-dashboard/src/app/dashboard/support-tickets/page.tsx:83` |
| ☐ | toast | ${replayTarget} has been re-processed. | OK |  | `admin-dashboard/src/app/dashboard/stripe-events/page.tsx:163` |
| ☐ | toast | ${res.addresses_inserted ?? 0} address(es) inserted. | OK |  | `admin-dashboard/src/app/dashboard/riders/legacy-saved-address-backfill/page.tsx:189` |
| ☐ | toast | ${res.count ?? 0} users exported. | OK |  | `admin-dashboard/src/app/dashboard/users/page.tsx:244` |
| ☐ | toast | ${res.count ?? res.drivers?.length ?? 0} drivers exported. | OK |  | `admin-dashboard/src/app/dashboard/drivers/page.tsx:772` |
| ☐ | toast | ${res.driver_count ?? 0} drivers indexed${res.failed ? | OK |  | `admin-dashboard/src/app/dashboard/monitoring/dispatch-geo/page.tsx:107` |
| ☐ | toast | ${res.fixed} driver row(s) corrected | OK |  | `admin-dashboard/src/app/dashboard/drivers/legacy-import/page.tsx:629` |
| ☐ | toast | ${res.fixed} driver row(s) created for ${res.scanned} orphaned account(s) | OK |  | `admin-dashboard/src/app/dashboard/drivers/legacy-import/page.tsx:537` |
| ☐ | toast | ${res.fixed} rider(s) corrected | OK |  | `admin-dashboard/src/app/dashboard/bulk-operations/page.tsx:1755` |
| ☐ | toast | ${res.history_rows_inserted ?? 0} history row(s) inserted. | OK |  | `admin-dashboard/src/app/dashboard/drivers/legacy-vehicle-history-backfill/page.tsx:185` |
| ☐ | toast | ${res.imported_drivers ?? 0} drivers imported. | OK |  | `admin-dashboard/src/app/dashboard/drivers/import/page.tsx:232` |
| ☐ | toast | ${res.new_drivers ?? 0} new, ${res.linked_accounts ?? 0} linked, ${res.enriched_drivers ?? 0} enriched. | OK |  | `admin-dashboard/src/app/dashboard/drivers/legacy-import/page.tsx:217` |
| ☐ | toast | ${res.success} succeeded, ${res.failed} failed | OK |  | `admin-dashboard/src/app/dashboard/bulk-operations/page.tsx:1203` |
| ☐ | toast | ${res.success} succeeded, ${res.failed} failed (${res.renderer} renderer) | OK |  | `admin-dashboard/src/app/dashboard/bulk-operations/page.tsx:1023` |
| ☐ | toast | ${res.updated ?? 0} driver(s) updated. | OK |  | `admin-dashboard/src/app/dashboard/drivers/legacy-sin-dob-backfill/page.tsx:196` |
| ☐ | toast | ${totals.updated} updated, ${totals.unchanged} already correct | OK |  | `admin-dashboard/src/app/dashboard/users/page.tsx:312` |
| ☐ | toast | AI chat failed | OK |  | `admin-dashboard/src/app/dashboard/ai-console/page.tsx:435` |
| ☐ | toast | AI provider catalog unavailable | OK |  | `admin-dashboard/src/app/dashboard/settings/page.tsx:127` |
| ☐ | toast | Action failed | OK |  | `admin-dashboard/src/app/dashboard/sentry-logs/page.tsx:276` |
| ☐ | toast | Action failed | OK |  | `admin-dashboard/src/app/dashboard/export-approvals/page.tsx:80` |
| ☐ | toast | Added to the do-not-market list. | OK |  | `admin-dashboard/src/app/dashboard/cloud-messaging/page.tsx:271` |
| ☐ | toast | Adjustment amount cannot be zero | OK |  | `admin-dashboard/src/app/dashboard/corporate-accounts/[id]/page.tsx:209` |
| ☐ | toast | Adjustment failed | OK |  | `admin-dashboard/src/app/dashboard/corporate-accounts/[id]/page.tsx:227` |
| ☐ | toast | Amount exceeds limit | OK |  | `admin-dashboard/src/app/dashboard/corporate-accounts/[id]/page.tsx:213` |
| ☐ | toast | Another replica currently holds the lock — safe to retry. | OK |  | `admin-dashboard/src/app/dashboard/monitoring/dispatch-geo/page.tsx:107` |
| ☐ | toast | Approval failed | OK |  | `admin-dashboard/src/app/dashboard/corporate-accounts/kyb-queue/page.tsx:83` |
| ☐ | toast | Assign failed | OK |  | `admin-dashboard/src/app/dashboard/support-tickets/tickets/[id]/page.tsx:237` |
| ☐ | toast | Backfill applied | OK |  | `admin-dashboard/src/app/dashboard/bulk-operations/page.tsx:1755` |
| ☐ | toast | Backfill applied | OK |  | `admin-dashboard/src/app/dashboard/drivers/legacy-import/page.tsx:537` |
| ☐ | toast | Backfill applied | OK |  | `admin-dashboard/src/app/dashboard/drivers/legacy-import/page.tsx:629` |
| ☐ | toast | Backfill complete | OK |  | `admin-dashboard/src/app/dashboard/riders/legacy-saved-address-backfill/page.tsx:189` |
| ☐ | toast | Backfill complete | OK |  | `admin-dashboard/src/app/dashboard/drivers/legacy-sin-dob-backfill/page.tsx:196` |
| ☐ | toast | Backfill complete | OK |  | `admin-dashboard/src/app/dashboard/drivers/legacy-vehicle-history-backfill/page.tsx:185` |
| ☐ | toast | Backfill failed | OK |  | `admin-dashboard/src/app/dashboard/bulk-operations/page.tsx:1762` |
| ☐ | toast | Backfill failed | OK |  | `admin-dashboard/src/app/dashboard/riders/legacy-saved-address-backfill/page.tsx:225` |
| ☐ | toast | Backfill failed | OK |  | `admin-dashboard/src/app/dashboard/drivers/legacy-import/page.tsx:544` |
| ☐ | toast | Backfill failed | OK |  | `admin-dashboard/src/app/dashboard/drivers/legacy-import/page.tsx:636` |
| ☐ | toast | Backfill failed | OK |  | `admin-dashboard/src/app/dashboard/drivers/legacy-sin-dob-backfill/page.tsx:232` |
| ☐ | toast | Backfill failed | OK |  | `admin-dashboard/src/app/dashboard/drivers/legacy-vehicle-history-backfill/page.tsx:220` |
| ☐ | toast | Backfill refused | OK |  | `admin-dashboard/src/app/dashboard/riders/legacy-saved-address-backfill/page.tsx:218` |
| ☐ | toast | Backfill refused | OK |  | `admin-dashboard/src/app/dashboard/drivers/legacy-sin-dob-backfill/page.tsx:225` |
| ☐ | toast | Backfill refused | OK |  | `admin-dashboard/src/app/dashboard/drivers/legacy-vehicle-history-backfill/page.tsx:213` |
| ☐ | setError | Booking failed | OK |  | `admin-dashboard/src/app/company-portal/[id]/book/page.tsx:322` |
| ☐ | toast | Both fields required | OK |  | `admin-dashboard/src/app/dashboard/driver-license-backfill/page.tsx:88` |
| ☐ | setError | Budget must be a non-negative number. | OK |  | `admin-dashboard/src/app/company-portal/[id]/sections/page.tsx:100` |
| ☐ | toast | Bulk refresh failed | OK |  | `admin-dashboard/src/app/dashboard/drivers/page.tsx:657` |
| ☐ | setError | Cancel failed | OK |  | `admin-dashboard/src/app/company-portal/[id]/bookings/page.tsx:92` |
| ☐ | toast | Charging your card on file. Your balance updates once the payment completes. | OK |  | `admin-dashboard/src/app/company-portal/[id]/billing/page.tsx:186` |
| ☐ | toast | Commit failed | OK |  | `admin-dashboard/src/app/dashboard/bulk-operations/page.tsx:530` |
| ☐ | toast | Commit failed | OK |  | `admin-dashboard/src/app/dashboard/bulk-operations/page.tsx:1520` |
| ☐ | toast | Commit refused | OK |  | `admin-dashboard/src/app/dashboard/bulk-operations/page.tsx:523` |
| ☐ | toast | Commit refused | OK |  | `admin-dashboard/src/app/dashboard/bulk-operations/page.tsx:1513` |
| ☐ | toast | Company added to billing pilot | OK |  | `admin-dashboard/src/app/dashboard/corporate-accounts/[id]/subscription/page.tsx:110` |
| ☐ | toast | Company created, but the owner invite failed | OK |  | `admin-dashboard/src/app/dashboard/corporate-accounts/page.tsx:262` |
| ☐ | toast | Company removed from billing pilot | OK |  | `admin-dashboard/src/app/dashboard/corporate-accounts/[id]/subscription/page.tsx:110` |
| ☐ | toast | Corporate subscription billing may not be enabled yet. | OK |  | `admin-dashboard/src/app/dashboard/corporate-accounts/[id]/subscription/page.tsx:96` |
| ☐ | toast | Could not add suppression. | OK |  | `admin-dashboard/src/app/dashboard/cloud-messaging/page.tsx:273` |
| ☐ | setError | Could not archive section | OK |  | `admin-dashboard/src/app/company-portal/[id]/sections/page.tsx:89` |
| ☐ | setError | Could not assign section | OK |  | `admin-dashboard/src/app/company-portal/[id]/sections/page.tsx:124` |
| ☐ | toast | Could not assign subscription | OK |  | `admin-dashboard/src/app/dashboard/corporate-accounts/[id]/subscription/page.tsx:96` |
| ☐ | toast | Could not charge your card on file. | OK |  | `admin-dashboard/src/app/company-portal/[id]/billing/page.tsx:194` |
| ☐ | toast | Could not commit the backfill | OK |  | `admin-dashboard/src/app/dashboard/riders/legacy-saved-address-backfill/page.tsx:225` |
| ☐ | toast | Could not commit the backfill | OK |  | `admin-dashboard/src/app/dashboard/drivers/legacy-sin-dob-backfill/page.tsx:232` |
| ☐ | toast | Could not commit the backfill | OK |  | `admin-dashboard/src/app/dashboard/drivers/legacy-vehicle-history-backfill/page.tsx:220` |
| ☐ | toast | Could not commit the import | OK |  | `admin-dashboard/src/app/dashboard/bulk-operations/page.tsx:1520` |
| ☐ | toast | Could not commit the import | OK |  | `admin-dashboard/src/app/dashboard/drivers/legacy-import/page.tsx:253` |
| ☐ | toast | Could not commit the import | OK |  | `admin-dashboard/src/app/dashboard/drivers/import/page.tsx:260` |
| ☐ | toast | Could not commit the mapping | OK |  | `admin-dashboard/src/app/dashboard/bulk-operations/page.tsx:530` |
| ☐ | setError | Could not create section | OK |  | `admin-dashboard/src/app/company-portal/[id]/sections/page.tsx:75` |
| ☐ | toast | Could not delete vehicle type | OK |  | `admin-dashboard/src/app/dashboard/vehicle-types/page.tsx:271` |
| ☐ | toast | Could not generate report | OK |  | `admin-dashboard/src/app/dashboard/compliance/page.tsx:197` |
| ☐ | toast | Could not generate the PDF. | OK |  | `admin-dashboard/src/app/company-portal/[id]/billing/page.tsx:159` |
| ☐ | toast | Could not generate the invoice PDF. | OK |  | `admin-dashboard/src/app/dashboard/subscriptions/page.tsx:582` |
| ☐ | toast | Could not load batch status | OK |  | `admin-dashboard/src/app/dashboard/bulk-operations/page.tsx:462` |
| ☐ | toast | Could not load document | OK |  | `admin-dashboard/src/app/dashboard/corporate-accounts/[id]/page.tsx:502` |
| ☐ | toast | Could not load document | OK |  | `admin-dashboard/src/app/dashboard/corporate-accounts/kyb-queue/page.tsx:118` |
| ☐ | toast | Could not load drivers | OK |  | `admin-dashboard/src/app/dashboard/driver-license-backfill/page.tsx:73` |
| ☐ | toast | Could not load incident | OK |  | `admin-dashboard/src/app/dashboard/safety/page.tsx:286` |
| ☐ | toast | Could not load issue | OK |  | `admin-dashboard/src/app/dashboard/sentry-logs/page.tsx:244` |
| ☐ | toast | Could not load queue | OK |  | `admin-dashboard/src/app/dashboard/export-approvals/page.tsx:50` |
| ☐ | toast | Could not load safety queue | OK |  | `admin-dashboard/src/app/dashboard/safety/page.tsx:231` |
| ☐ | toast | Could not log incident | OK |  | `admin-dashboard/src/app/dashboard/safety/page.tsx:610` |
| ☐ | toast | Could not read Stripe accounts | OK |  | `admin-dashboard/src/app/dashboard/bulk-operations/page.tsx:429` |
| ☐ | setError | Could not register the company | OK |  | `admin-dashboard/src/app/company-signup/page.tsx:134` |
| ☐ | toast | Could not reload documents | OK |  | `admin-dashboard/src/app/dashboard/drivers/page.tsx:415` |
| ☐ | toast | Could not remove suppression. | OK |  | `admin-dashboard/src/app/dashboard/cloud-messaging/page.tsx:282` |
| ☐ | toast | Could not save vehicle type. Please try again. | OK |  | `admin-dashboard/src/app/dashboard/vehicle-types/page.tsx:253` |
| ☐ | setError | Could not send code | OK |  | `admin-dashboard/src/app/company-login/page.tsx:50` |
| ☐ | setError | Could not send code | OK |  | `admin-dashboard/src/app/company-signup/page.tsx:78` |
| ☐ | setError | Could not set budget | OK |  | `admin-dashboard/src/app/company-portal/[id]/sections/page.tsx:113` |
| ☐ | toast | Could not update this driver's Stripe account. | OK |  | `admin-dashboard/src/app/dashboard/bulk-operations/page.tsx:195` |
| ☐ | toast | Could not update vehicle type status. Please try again. | OK |  | `admin-dashboard/src/app/dashboard/vehicle-types/page.tsx:288` |
| ☐ | toast | Could not validate the CSV | OK |  | `admin-dashboard/src/app/dashboard/bulk-operations/page.tsx:447` |
| ☐ | toast | Could not validate the CSV | OK |  | `admin-dashboard/src/app/dashboard/bulk-operations/page.tsx:1465` |
| ☐ | toast | Could not validate the CSV | OK |  | `admin-dashboard/src/app/dashboard/drivers/legacy-import/page.tsx:181` |
| ☐ | toast | Could not validate the CSV | OK |  | `admin-dashboard/src/app/dashboard/drivers/import/page.tsx:201` |
| ☐ | toast | Could not validate the CSVs | OK |  | `admin-dashboard/src/app/dashboard/riders/legacy-saved-address-backfill/page.tsx:158` |
| ☐ | toast | Could not validate the CSVs | OK |  | `admin-dashboard/src/app/dashboard/drivers/legacy-sin-dob-backfill/page.tsx:165` |
| ☐ | toast | Could not validate the CSVs | OK |  | `admin-dashboard/src/app/dashboard/drivers/legacy-vehicle-history-backfill/page.tsx:154` |
| ☐ | toast | Couldn't draft a reply | OK |  | `admin-dashboard/src/app/dashboard/support-tickets/tickets/[id]/page.tsx:250` |
| ☐ | toast | Couldn't load heat map settings | OK |  | `admin-dashboard/src/app/dashboard/settings/page.tsx:137` |
| ☐ | toast | Couldn't load settings | OK |  | `admin-dashboard/src/app/dashboard/settings/page.tsx:106` |
| ☐ | toast | Couldn't refresh MFA status | OK |  | `admin-dashboard/src/app/dashboard/settings/page.tsx:157` |
| ☐ | toast | Create failed | OK |  | `admin-dashboard/src/app/dashboard/support-tickets/tickets/page.tsx:196` |
| ☐ | toast | Create the vehicle type before uploading a custom marker so we can key the file by id. | OK |  | `admin-dashboard/src/app/dashboard/vehicle-types/page.tsx:147` |
| ☐ | toast | Create the vehicle type before uploading an illustration so we can key the file by id. | OK |  | `admin-dashboard/src/app/dashboard/vehicle-types/page.tsx:173` |
| ☐ | toast | Custom marker uploaded | OK |  | `admin-dashboard/src/app/dashboard/vehicle-types/page.tsx:162` |
| ☐ | toast | Decision failed | OK |  | `admin-dashboard/src/app/dashboard/corporate-accounts/[id]/members/page.tsx:250` |
| ☐ | toast | Deleted ${res.deleted_keys} keys under ${flushCandidate.prefix} | OK |  | `admin-dashboard/src/app/dashboard/monitoring/redis/page.tsx:214` |
| ☐ | toast | Discovery failed | OK |  | `admin-dashboard/src/app/dashboard/bulk-operations/page.tsx:429` |
| ☐ | toast | Dismiss failed | OK |  | `admin-dashboard/src/app/dashboard/stripe-events/page.tsx:187` |
| ☐ | toast | Dispatch geo status updated | OK |  | `admin-dashboard/src/app/dashboard/monitoring/dispatch-geo/page.tsx:97` |
| ☐ | setError | Document must be a PDF, PNG, or JPEG. | OK |  | `admin-dashboard/src/app/company-portal/[id]/verification/page.tsx:75` |
| ☐ | setError | Document must be under 10 MB. | OK |  | `admin-dashboard/src/app/company-portal/[id]/verification/page.tsx:79` |
| ☐ | toast | Document review failed | OK |  | `admin-dashboard/src/app/dashboard/drivers/page.tsx:429` |
| ☐ | toast | Download failed | OK |  | `admin-dashboard/src/app/dashboard/subscriptions/page.tsx:582` |
| ☐ | toast | Draft ready | OK |  | `admin-dashboard/src/app/dashboard/support-tickets/tickets/[id]/page.tsx:248` |
| ☐ | toast | Driver flag disabled | OK |  | `admin-dashboard/src/app/dashboard/users/page.tsx:835` |
| ☐ | toast | Driver flag enabled | OK |  | `admin-dashboard/src/app/dashboard/users/page.tsx:835` |
| ☐ | toast | Driver list may be incomplete | OK |  | `admin-dashboard/src/app/dashboard/drivers/decals/page.tsx:114` |
| ☐ | toast | Driver now paid to ${it.new_stripe_account_id}.${note} | OK |  | `admin-dashboard/src/app/dashboard/bulk-operations/page.tsx:190` |
| ☐ | toast | Emailed to the driver's address on file. | OK |  | `admin-dashboard/src/app/dashboard/subscriptions/page.tsx:593` |
| ☐ | setError | Enrollment succeeded but sign-in failed. Please log in again. | OK |  | `admin-dashboard/src/app/login/page.tsx:165` |
| ☐ | setError | Enter OTP | OK |  | `admin-dashboard/src/app/register/driver/page.tsx:100` |
| ☐ | toast | Enter an amount between $100 and $10,000. | OK |  | `admin-dashboard/src/app/company-portal/[id]/billing/page.tsx:176` |
| ☐ | toast | Enter the license number and class before saving. | OK |  | `admin-dashboard/src/app/dashboard/driver-license-backfill/page.tsx:88` |
| ☐ | toast | Event dismissed | OK |  | `admin-dashboard/src/app/dashboard/stripe-events/page.tsx:182` |
| ☐ | toast | Event replayed | OK |  | `admin-dashboard/src/app/dashboard/stripe-events/page.tsx:163` |
| ☐ | setError | Every time window end must be after its start. | OK |  | `admin-dashboard/src/app/company-portal/[id]/policy/page.tsx:156` |
| ☐ | toast | Export complete | OK |  | `admin-dashboard/src/app/dashboard/users/page.tsx:244` |
| ☐ | toast | Export complete | OK |  | `admin-dashboard/src/app/dashboard/drivers/page.tsx:772` |
| ☐ | toast | Export failed | OK |  | `admin-dashboard/src/app/dashboard/users/page.tsx:246` |
| ☐ | toast | Export failed | OK |  | `admin-dashboard/src/app/dashboard/drivers/page.tsx:774` |
| ☐ | toast | Failed to activate user | OK |  | `admin-dashboard/src/app/dashboard/users/page.tsx:758` |
| ☐ | toast | Failed to cancel message | OK |  | `admin-dashboard/src/app/dashboard/cloud-messaging/page.tsx:371` |
| ☐ | toast | Failed to cancel ride | OK |  | `admin-dashboard/src/app/dashboard/monitoring/page.tsx:946` |
| ☐ | toast | Failed to cancel subscription | OK |  | `admin-dashboard/src/app/dashboard/corporate-accounts/[id]/subscription/page.tsx:130` |
| ☐ | setError | Failed to check affected rides | OK |  | `admin-dashboard/src/app/dashboard/corporate-accounts/[id]/policy/page.tsx:210` |
| ☐ | setError | Failed to check affected rides | OK |  | `admin-dashboard/src/app/company-portal/[id]/policy/page.tsx:167` |
| ☐ | toast | Failed to complete ride | OK |  | `admin-dashboard/src/app/dashboard/monitoring/page.tsx:903` |
| ☐ | toast | Failed to delete account | OK |  | `admin-dashboard/src/app/dashboard/corporate-accounts/page.tsx:299` |
| ☐ | toast | Failed to delete promo | OK |  | `admin-dashboard/src/app/dashboard/promotions/page.tsx:465` |
| ☐ | toast | Failed to delete staff member | OK |  | `admin-dashboard/src/app/dashboard/staff/page.tsx:223` |
| ☐ | toast | Failed to download invoice | OK |  | `admin-dashboard/src/app/company-portal/[id]/billing/page.tsx:159` |
| ☐ | setError | Failed to fetch ride data | OK |  | `admin-dashboard/src/app/dashboard/rides/live/[id]/page.tsx:37` |
| ☐ | toast | Failed to generate welcome letter | OK |  | `admin-dashboard/src/app/dashboard/drivers/decals/page.tsx:214` |
| ☐ | toast | Failed to generate welcome letters | OK |  | `admin-dashboard/src/app/dashboard/drivers/decals/page.tsx:237` |
| ☐ | setError | Failed to load | OK |  | `admin-dashboard/src/app/dashboard/support-tickets/page.tsx:73` |
| ☐ | setError | Failed to load | OK |  | `admin-dashboard/src/app/company-portal/page.tsx:34` |
| ☐ | setError | Failed to load | OK |  | `admin-dashboard/src/app/company-portal/[id]/overview/page.tsx:55` |
| ☐ | setError | Failed to load | OK |  | `admin-dashboard/src/app/company-portal/[id]/activity/page.tsx:59` |
| ☐ | setError | Failed to load | OK |  | `admin-dashboard/src/app/company-portal/[id]/policy/page.tsx:92` |
| ☐ | setError | Failed to load | OK |  | `admin-dashboard/src/app/company-portal/[id]/settings/page.tsx:32` |
| ☐ | setError | Failed to load | OK |  | `admin-dashboard/src/app/company-portal/[id]/billing/page.tsx:124` |
| ☐ | setError | Failed to load | OK |  | `admin-dashboard/src/app/company-portal/[id]/allowances/page.tsx:73` |
| ☐ | setError | Failed to load | OK |  | `admin-dashboard/src/app/company-portal/[id]/members/page.tsx:69` |
| ☐ | setError | Failed to load | OK |  | `admin-dashboard/src/app/company-portal/[id]/allowance-requests/page.tsx:65` |
| ☐ | setError | Failed to load FAQs. Please try again. | OK |  | `admin-dashboard/src/app/dashboard/faqs/page.tsx:95` |
| ☐ | setError | Failed to load KYB queue | OK |  | `admin-dashboard/src/app/dashboard/corporate-accounts/kyb-queue/page.tsx:67` |
| ☐ | setError | Failed to load Redis stats | FLAG | technical term: redis | `admin-dashboard/src/app/dashboard/monitoring/redis/page.tsx:141` |
| ☐ | setError | Failed to load Sentry config | OK |  | `admin-dashboard/src/app/dashboard/sentry-logs/page.tsx:193` |
| ☐ | setError | Failed to load Sentry issues | OK |  | `admin-dashboard/src/app/dashboard/sentry-logs/page.tsx:172` |
| ☐ | toast | Failed to load accounts | OK |  | `admin-dashboard/src/app/dashboard/corporate-accounts/page.tsx:205` |
| ☐ | setError | Failed to load bookings | OK |  | `admin-dashboard/src/app/company-portal/[id]/bookings/page.tsx:72` |
| ☐ | setError | Failed to load company | OK |  | `admin-dashboard/src/app/dashboard/corporate-accounts/[id]/page.tsx:178` |
| ☐ | toast | Failed to load detail | OK |  | `admin-dashboard/src/app/dashboard/stripe-events/page.tsx:147` |
| ☐ | setError | Failed to load dispatch geo status | OK |  | `admin-dashboard/src/app/dashboard/monitoring/dispatch-geo/page.tsx:80` |
| ☐ | toast | Failed to load drivers | OK |  | `admin-dashboard/src/app/dashboard/drivers/decals/page.tsx:121` |
| ☐ | toast | Failed to load expiring docs | OK |  | `admin-dashboard/src/app/dashboard/drivers/expiring/page.tsx:71` |
| ☐ | setError | Failed to load policy | OK |  | `admin-dashboard/src/app/dashboard/corporate-accounts/[id]/policy/page.tsx:145` |
| ☐ | toast | Failed to load queue | OK |  | `admin-dashboard/src/app/dashboard/drivers/queue/page.tsx:108` |
| ☐ | setError | Failed to load sections | OK |  | `admin-dashboard/src/app/company-portal/[id]/sections/page.tsx:56` |
| ☐ | toast | Failed to load staff | OK |  | `admin-dashboard/src/app/dashboard/staff/page.tsx:145` |
| ☐ | toast | Failed to load subscription | OK |  | `admin-dashboard/src/app/dashboard/corporate-accounts/[id]/subscription/page.tsx:74` |
| ☐ | setError | Failed to load ticket | OK |  | `admin-dashboard/src/app/dashboard/support-tickets/tickets/[id]/page.tsx:200` |
| ☐ | setError | Failed to load tickets | OK |  | `admin-dashboard/src/app/dashboard/support-tickets/tickets/page.tsx:228` |
| ☐ | setError | Failed to load trends | OK |  | `admin-dashboard/src/app/dashboard/support-tickets/trends/page.tsx:73` |
| ☐ | setError | Failed to load users. Please try again. | OK |  | `admin-dashboard/src/app/dashboard/users/page.tsx:191` |
| ☐ | toast | Failed to load vehicle types | OK |  | `admin-dashboard/src/app/dashboard/vehicle-types/page.tsx:202` |
| ☐ | setError | Failed to load venues | OK |  | `admin-dashboard/src/app/dashboard/venues/page.tsx:63` |
| ☐ | setError | Failed to load verification state | OK |  | `admin-dashboard/src/app/company-portal/[id]/verification/page.tsx:57` |
| ☐ | toast | Failed to reset MFA | OK |  | `admin-dashboard/src/app/dashboard/staff/page.tsx:239` |
| ☐ | toast | Failed to save | OK |  | `admin-dashboard/src/app/dashboard/promotions/page.tsx:439` |
| ☐ | toast | Failed to save account | OK |  | `admin-dashboard/src/app/dashboard/corporate-accounts/page.tsx:284` |
| ☐ | toast | Failed to save driver | OK |  | `admin-dashboard/src/app/dashboard/drivers/page.tsx:561` |
| ☐ | setError | Failed to save plan. Please try again. | OK |  | `admin-dashboard/src/app/dashboard/subscriptions/page.tsx:187` |
| ☐ | toast | Failed to save profile | OK |  | `admin-dashboard/src/app/dashboard/corporate-accounts/[id]/page.tsx:275` |
| ☐ | toast | Failed to save staff member | OK |  | `admin-dashboard/src/app/dashboard/staff/page.tsx:185` |
| ☐ | setError | Failed to save tax config. Please try again. | OK |  | `admin-dashboard/src/app/dashboard/subscriptions/page.tsx:385` |
| ☐ | setError | Failed to save. Please try again. | OK |  | `admin-dashboard/src/app/dashboard/documents/requirements/page.tsx:98` |
| ☐ | toast | Failed to send | OK |  | `admin-dashboard/src/app/dashboard/cloud-messaging/page.tsx:351` |
| ☐ | toast | Failed to toggle auto top-up | OK |  | `admin-dashboard/src/app/dashboard/corporate-accounts/[id]/page.tsx:242` |
| ☐ | toast | Failed to toggle promo | OK |  | `admin-dashboard/src/app/dashboard/promotions/page.tsx:451` |
| ☐ | toast | Failed to update flag | OK |  | `admin-dashboard/src/app/dashboard/users/page.tsx:817` |
| ☐ | toast | Failed to update flag | OK |  | `admin-dashboard/src/app/dashboard/users/page.tsx:837` |
| ☐ | toast | Failed to update pilot status | OK |  | `admin-dashboard/src/app/dashboard/corporate-accounts/[id]/subscription/page.tsx:115` |
| ☐ | toast | Failed to update staff | OK |  | `admin-dashboard/src/app/dashboard/staff/page.tsx:208` |
| ☐ | toast | Failed to update user status | OK |  | `admin-dashboard/src/app/dashboard/users/page.tsx:1184` |
| ☐ | toast | File too large | OK |  | `admin-dashboard/src/app/dashboard/vehicle-types/page.tsx:155` |
| ☐ | toast | File too large | OK |  | `admin-dashboard/src/app/dashboard/vehicle-types/page.tsx:181` |
| ☐ | toast | File too large | OK |  | `admin-dashboard/src/app/dashboard/settings/page.tsx:59` |
| ☐ | setError | Fix time window errors before saving. | OK |  | `admin-dashboard/src/app/dashboard/corporate-accounts/[id]/policy/page.tsx:199` |
| ☐ | toast | Flush failed | OK |  | `admin-dashboard/src/app/dashboard/monitoring/redis/page.tsx:221` |
| ☐ | toast | Found ${res.fixed} driver row(s) with the wrong Joined date | OK |  | `admin-dashboard/src/app/dashboard/drivers/legacy-import/page.tsx:629` |
| ☐ | toast | Found ${res.fixed} orphaned account(s) out of ${res.scanned} scanned | OK |  | `admin-dashboard/src/app/dashboard/drivers/legacy-import/page.tsx:537` |
| ☐ | toast | Found ${res.fixed} rider(s) with the wrong Joined date | OK |  | `admin-dashboard/src/app/dashboard/bulk-operations/page.tsx:1755` |
| ☐ | toast | Generated welcome letters for ${ids.length} driver(s). | OK |  | `admin-dashboard/src/app/dashboard/drivers/decals/page.tsx:231` |
| ☐ | toast | Go to Migration Checklist | OK |  | `admin-dashboard/src/app/dashboard/riders/legacy-saved-address-backfill/page.tsx:189` |
| ☐ | toast | Go to Migration Checklist | OK |  | `admin-dashboard/src/app/dashboard/drivers/legacy-import/page.tsx:217` |
| ☐ | toast | Go to Migration Checklist | OK |  | `admin-dashboard/src/app/dashboard/drivers/legacy-sin-dob-backfill/page.tsx:196` |
| ☐ | toast | Go to Migration Checklist | OK |  | `admin-dashboard/src/app/dashboard/drivers/import/page.tsx:232` |
| ☐ | toast | Go to Migration Checklist | OK |  | `admin-dashboard/src/app/dashboard/drivers/legacy-vehicle-history-backfill/page.tsx:185` |
| ☐ | toast | H3 index rebuilt | OK |  | `admin-dashboard/src/app/dashboard/monitoring/dispatch-geo/page.tsx:107` |
| ☐ | toast | Heat map settings not saved | OK |  | `admin-dashboard/src/app/dashboard/settings/page.tsx:247` |
| ☐ | toast | Heat map settings saved | OK |  | `admin-dashboard/src/app/dashboard/settings/page.tsx:245` |
| ☐ | toast | Illustration uploaded | OK |  | `admin-dashboard/src/app/dashboard/vehicle-types/page.tsx:188` |
| ☐ | toast | Import complete | OK |  | `admin-dashboard/src/app/dashboard/drivers/legacy-import/page.tsx:217` |
| ☐ | toast | Import complete | OK |  | `admin-dashboard/src/app/dashboard/drivers/import/page.tsx:232` |
| ☐ | toast | Import failed | OK |  | `admin-dashboard/src/app/dashboard/drivers/legacy-import/page.tsx:253` |
| ☐ | toast | Import failed | OK |  | `admin-dashboard/src/app/dashboard/drivers/import/page.tsx:260` |
| ☐ | toast | Import refused | OK |  | `admin-dashboard/src/app/dashboard/drivers/legacy-import/page.tsx:246` |
| ☐ | toast | Import refused | OK |  | `admin-dashboard/src/app/dashboard/drivers/import/page.tsx:253` |
| ☐ | toast | Incident logged | OK |  | `admin-dashboard/src/app/dashboard/safety/page.tsx:606` |
| ☐ | toast | Incident updated | OK |  | `admin-dashboard/src/app/dashboard/safety/page.tsx:747` |
| ☐ | toast | Internal note added | OK |  | `admin-dashboard/src/app/dashboard/support-tickets/tickets/[id]/page.tsx:298` |
| ☐ | toast | Invalid amount | OK |  | `admin-dashboard/src/app/dashboard/corporate-accounts/[id]/page.tsx:209` |
| ☐ | toast | Invalid amount | OK |  | `admin-dashboard/src/app/company-portal/[id]/billing/page.tsx:176` |
| ☐ | setError | Invalid code | OK |  | `admin-dashboard/src/app/company-login/page.tsx:102` |
| ☐ | setError | Invalid code | OK |  | `admin-dashboard/src/app/company-signup/page.tsx:96` |
| ☐ | setError | Invalid code. Please try again. | OK |  | `admin-dashboard/src/app/login/page.tsx:86` |
| ☐ | setError | Invalid credentials | OK |  | `admin-dashboard/src/app/login/page.tsx:119` |
| ☐ | toast | Invalid email address | OK |  | `admin-dashboard/src/app/dashboard/corporate-accounts/[id]/members/page.tsx:198` |
| ☐ | toast | Invalid file | OK |  | `admin-dashboard/src/app/dashboard/drivers/page.tsx:586` |
| ☐ | setError | Invite failed | OK |  | `admin-dashboard/src/app/company-portal/[id]/members/page.tsx:112` |
| ☐ | toast | Invite failed | OK |  | `admin-dashboard/src/app/dashboard/corporate-accounts/[id]/members/page.tsx:208` |
| ☐ | toast | Invite link copied to clipboard. | OK |  | `admin-dashboard/src/app/dashboard/corporate-accounts/page.tsx:274` |
| ☐ | toast | Invoice sent | OK |  | `admin-dashboard/src/app/dashboard/subscriptions/page.tsx:593` |
| ☐ | toast | Issue ${issueId} is now ${newStatus}. | OK |  | `admin-dashboard/src/app/dashboard/sentry-logs/page.tsx:260` |
| ☐ | toast | Issue ${newStatus} | OK |  | `admin-dashboard/src/app/dashboard/sentry-logs/page.tsx:260` |
| ☐ | toast | Issue resolved | OK |  | `admin-dashboard/src/app/dashboard/sentry-logs/page.tsx:260` |
| ☐ | toast | KYC refresh: ${res.total} driver${res.total === 1 ? "" : "s"} | OK |  | `admin-dashboard/src/app/dashboard/drivers/page.tsx:650` |
| ☐ | toast | Loaded the first ${all.length.toLocaleString()} drivers matching your filters — narrow the Status or Area filter to see the rest. | OK |  | `admin-dashboard/src/app/dashboard/drivers/decals/page.tsx:114` |
| ☐ | toast | MFA disabled | OK |  | `admin-dashboard/src/app/dashboard/settings/page.tsx:175` |
| ☐ | toast | MFA reset | OK |  | `admin-dashboard/src/app/dashboard/staff/page.tsx:233` |
| ☐ | toast | Mapping CSV downloaded | OK |  | `admin-dashboard/src/app/dashboard/bulk-operations/page.tsx:427` |
| ☐ | toast | Mapping committed | OK |  | `admin-dashboard/src/app/dashboard/bulk-operations/page.tsx:506` |
| ☐ | toast | Marked as duplicate | OK |  | `admin-dashboard/src/app/dashboard/safety/page.tsx:722` |
| ☐ | toast | Max 500 KB. | OK |  | `admin-dashboard/src/app/dashboard/vehicle-types/page.tsx:155` |
| ☐ | toast | Max 500 KB. | OK |  | `admin-dashboard/src/app/dashboard/vehicle-types/page.tsx:181` |
| ☐ | toast | Max 500 KB. | OK |  | `admin-dashboard/src/app/dashboard/settings/page.tsx:59` |
| ☐ | toast | Merge failed | OK |  | `admin-dashboard/src/app/dashboard/safety/page.tsx:724` |
| ☐ | toast | Merged into ${targetId.slice(0, 8)}… | OK |  | `admin-dashboard/src/app/dashboard/safety/page.tsx:722` |
| ☐ | setError | Name is required. | OK |  | `admin-dashboard/src/app/dashboard/documents/requirements/page.tsx:92` |
| ☐ | toast | No drivers with a Stripe account. | OK |  | `admin-dashboard/src/app/dashboard/drivers/page.tsx:650` |
| ☐ | toast | No eligible drivers selected | OK |  | `admin-dashboard/src/app/dashboard/drivers/decals/page.tsx:223` |
| ☐ | toast | No importable matches | OK |  | `admin-dashboard/src/app/dashboard/bulk-operations/page.tsx:415` |
| ☐ | toast | Note failed | OK |  | `admin-dashboard/src/app/dashboard/support-tickets/tickets/[id]/page.tsx:301` |
| ☐ | toast | Nothing to correct | OK |  | `admin-dashboard/src/app/dashboard/drivers/page.tsx:696` |
| ☐ | toast | Nothing to download. | OK |  | `admin-dashboard/src/app/dashboard/bulk-operations/page.tsx:415` |
| ☐ | toast | Nothing to save | OK |  | `admin-dashboard/src/app/dashboard/safety/page.tsx:740` |
| ☐ | toast | Nothing to sync | OK |  | `admin-dashboard/src/app/dashboard/users/page.tsx:266` |
| ☐ | toast | Nudge failed | OK |  | `admin-dashboard/src/app/dashboard/drivers/expiring/page.tsx:89` |
| ☐ | toast | Operation successful | OK |  | `admin-dashboard/src/app/dashboard/users/page.tsx:138` |
| ☐ | toast | Owner invited | OK |  | `admin-dashboard/src/app/dashboard/corporate-accounts/page.tsx:274` |
| ☐ | toast | Owner invited | OK |  | `admin-dashboard/src/app/dashboard/corporate-accounts/page.tsx:276` |
| ☐ | toast | Payout account updated | OK |  | `admin-dashboard/src/app/dashboard/bulk-operations/page.tsx:190` |
| ☐ | toast | Payout sync failed | OK |  | `admin-dashboard/src/app/dashboard/drivers/page.tsx:681` |
| ☐ | setError | Phone number is required | OK |  | `admin-dashboard/src/app/register/driver/page.tsx:81` |
| ☐ | toast | Photo ${next} | OK |  | `admin-dashboard/src/app/dashboard/drivers/page.tsx:573` |
| ☐ | toast | Photo approved | OK |  | `admin-dashboard/src/app/dashboard/drivers/queue/page.tsx:128` |
| ☐ | toast | Photo rejected | OK |  | `admin-dashboard/src/app/dashboard/drivers/queue/page.tsx:128` |
| ☐ | toast | Photo review failed | OK |  | `admin-dashboard/src/app/dashboard/drivers/page.tsx:575` |
| ☐ | toast | Photo review failed | OK |  | `admin-dashboard/src/app/dashboard/drivers/queue/page.tsx:131` |
| ☐ | toast | Photo upload failed | OK |  | `admin-dashboard/src/app/dashboard/drivers/page.tsx:598` |
| ☐ | toast | Photo uploaded | OK |  | `admin-dashboard/src/app/dashboard/drivers/page.tsx:596` |
| ☐ | setError | Plan name is required. | OK |  | `admin-dashboard/src/app/dashboard/subscriptions/page.tsx:177` |
| ☐ | toast | Please choose an image (JPEG, PNG, WebP, or GIF). | OK |  | `admin-dashboard/src/app/dashboard/drivers/page.tsx:586` |
| ☐ | toast | Please enter a valid email | OK |  | `admin-dashboard/src/app/dashboard/corporate-accounts/[id]/members/page.tsx:198` |
| ☐ | toast | Please refresh the page. | OK |  | `admin-dashboard/src/app/dashboard/vehicle-types/page.tsx:202` |
| ☐ | toast | Please try again. | OK |  | `admin-dashboard/src/app/dashboard/corporate-accounts/page.tsx:284` |
| ☐ | toast | Prefix flushed | OK |  | `admin-dashboard/src/app/dashboard/monitoring/redis/page.tsx:214` |
| ☐ | toast | Preview failed | OK |  | `admin-dashboard/src/app/dashboard/bulk-operations/page.tsx:1002` |
| ☐ | toast | Preview failed | OK |  | `admin-dashboard/src/app/dashboard/bulk-operations/page.tsx:1182` |
| ☐ | setError | Price must be ≥ 0. | OK |  | `admin-dashboard/src/app/dashboard/subscriptions/page.tsx:178` |
| ☐ | toast | Promo ${!p.is_active ? 'activated' : 'deactivated'} | OK |  | `admin-dashboard/src/app/dashboard/promotions/page.tsx:448` |
| ☐ | toast | Promo created | OK |  | `admin-dashboard/src/app/dashboard/promotions/page.tsx:434` |
| ☐ | toast | Promo deleted | OK |  | `admin-dashboard/src/app/dashboard/promotions/page.tsx:462` |
| ☐ | toast | Promo updated | OK |  | `admin-dashboard/src/app/dashboard/promotions/page.tsx:434` |
| ☐ | toast | Re-invite the owner from the company's Members page. | OK |  | `admin-dashboard/src/app/dashboard/corporate-accounts/page.tsx:262` |
| ☐ | toast | Rebuild failed | OK |  | `admin-dashboard/src/app/dashboard/monitoring/dispatch-geo/page.tsx:115` |
| ☐ | toast | Rebuild skipped | OK |  | `admin-dashboard/src/app/dashboard/monitoring/dispatch-geo/page.tsx:107` |
| ☐ | toast | Recompute failed | OK |  | `admin-dashboard/src/app/dashboard/drivers/page.tsx:724` |
| ☐ | toast | Redis + infra stats updated | FLAG | technical term: redis | `admin-dashboard/src/app/dashboard/monitoring/redis/page.tsx:203` |
| ☐ | toast | Ref: ${result.audit_log_id} | OK |  | `admin-dashboard/src/app/dashboard/users/page.tsx:138` |
| ☐ | toast | Ref: ${updated.audit_log_id} | OK |  | `admin-dashboard/src/app/dashboard/settings/page.tsx:192` |
| ☐ | toast | Rejection failed | OK |  | `admin-dashboard/src/app/dashboard/corporate-accounts/kyb-queue/page.tsx:102` |
| ☐ | toast | Reminder sent | OK |  | `admin-dashboard/src/app/dashboard/drivers/expiring/page.tsx:86` |
| ☐ | toast | Remove failed | OK |  | `admin-dashboard/src/app/dashboard/corporate-accounts/[id]/members/page.tsx:224` |
| ☐ | toast | Replay failed | OK |  | `admin-dashboard/src/app/dashboard/stripe-events/page.tsx:167` |
| ☐ | toast | Reply failed | OK |  | `admin-dashboard/src/app/dashboard/support-tickets/tickets/[id]/page.tsx:279` |
| ☐ | toast | Reply sent | OK |  | `admin-dashboard/src/app/dashboard/support-tickets/tickets/[id]/page.tsx:276` |
| ☐ | setError | Retry failed. Please try again. | OK |  | `admin-dashboard/src/app/dashboard/earnings/payouts/page.tsx:90` |
| ☐ | toast | Review and edit before sending. | OK |  | `admin-dashboard/src/app/dashboard/support-tickets/tickets/[id]/page.tsx:248` |
| ☐ | toast | Ride-offer sound uploaded | OK |  | `admin-dashboard/src/app/dashboard/settings/page.tsx:66` |
| ☐ | toast | Rider flag disabled | OK |  | `admin-dashboard/src/app/dashboard/users/page.tsx:815` |
| ☐ | toast | Rider flag enabled | OK |  | `admin-dashboard/src/app/dashboard/users/page.tsx:815` |
| ☐ | toast | Rider import committed | OK |  | `admin-dashboard/src/app/dashboard/bulk-operations/page.tsx:1496` |
| ☐ | toast | Route generation failed | OK |  | `admin-dashboard/src/app/dashboard/bulk-operations/page.tsx:1208` |
| ☐ | toast | Routes regenerated | OK |  | `admin-dashboard/src/app/dashboard/bulk-operations/page.tsx:1203` |
| ☐ | setError | Save failed | OK |  | `admin-dashboard/src/app/dashboard/corporate-accounts/[id]/policy/page.tsx:185` |
| ☐ | setError | Save failed | OK |  | `admin-dashboard/src/app/company-portal/[id]/policy/page.tsx:141` |
| ☐ | setError | Save failed | OK |  | `admin-dashboard/src/app/company-portal/[id]/allowances/page.tsx:100` |
| ☐ | toast | Save failed | OK |  | `admin-dashboard/src/app/dashboard/vehicle-types/page.tsx:253` |
| ☐ | toast | Save failed | OK |  | `admin-dashboard/src/app/dashboard/driver-license-backfill/page.tsx:100` |
| ☐ | toast | Save the type first | OK |  | `admin-dashboard/src/app/dashboard/vehicle-types/page.tsx:147` |
| ☐ | toast | Save the type first | OK |  | `admin-dashboard/src/app/dashboard/vehicle-types/page.tsx:173` |
| ☐ | toast | Scan complete | OK |  | `admin-dashboard/src/app/dashboard/bulk-operations/page.tsx:1755` |
| ☐ | toast | Scan complete | OK |  | `admin-dashboard/src/app/dashboard/drivers/legacy-import/page.tsx:537` |
| ☐ | toast | Scan complete | OK |  | `admin-dashboard/src/app/dashboard/drivers/legacy-import/page.tsx:629` |
| ☐ | toast | Scan failed | OK |  | `admin-dashboard/src/app/dashboard/bulk-operations/page.tsx:1762` |
| ☐ | toast | Scan failed | OK |  | `admin-dashboard/src/app/dashboard/drivers/legacy-import/page.tsx:544` |
| ☐ | toast | Scan failed | OK |  | `admin-dashboard/src/app/dashboard/drivers/legacy-import/page.tsx:636` |
| ☐ | toast | Select drivers that haven't had welcome letters generated yet. | OK |  | `admin-dashboard/src/app/dashboard/drivers/decals/page.tsx:223` |
| ☐ | toast | Send failed | OK |  | `admin-dashboard/src/app/dashboard/subscriptions/page.tsx:596` |
| ☐ | toast | Service area assigned | OK |  | `admin-dashboard/src/app/dashboard/support-tickets/tickets/[id]/page.tsx:235` |
| ☐ | toast | Service area cleared | OK |  | `admin-dashboard/src/app/dashboard/support-tickets/tickets/[id]/page.tsx:235` |
| ☐ | toast | Settings not saved | OK |  | `admin-dashboard/src/app/dashboard/settings/page.tsx:219` |
| ☐ | toast | Settings saved | OK |  | `admin-dashboard/src/app/dashboard/settings/page.tsx:192` |
| ☐ | toast | Single adjustment cannot exceed $${MAX_SINGLE_ADJUSTMENT.toFixed(2)} | OK |  | `admin-dashboard/src/app/dashboard/corporate-accounts/[id]/page.tsx:213` |
| ☐ | toast | Snapshot generation failed | OK |  | `admin-dashboard/src/app/dashboard/bulk-operations/page.tsx:1028` |
| ☐ | toast | Snapshots regenerated | OK |  | `admin-dashboard/src/app/dashboard/bulk-operations/page.tsx:1023` |
| ☐ | toast | Staff member ${!s.is_active ? 'activated' : 'deactivated'} | OK |  | `admin-dashboard/src/app/dashboard/staff/page.tsx:205` |
| ☐ | toast | Staff member created | OK |  | `admin-dashboard/src/app/dashboard/staff/page.tsx:181` |
| ☐ | toast | Staff member deleted | OK |  | `admin-dashboard/src/app/dashboard/staff/page.tsx:220` |
| ☐ | toast | Staff member updated | OK |  | `admin-dashboard/src/app/dashboard/staff/page.tsx:181` |
| ☐ | toast | Statement totals updated | OK |  | `admin-dashboard/src/app/dashboard/drivers/page.tsx:716` |
| ☐ | toast | Status change failed | OK |  | `admin-dashboard/src/app/dashboard/corporate-accounts/[id]/page.tsx:299` |
| ☐ | toast | Status change failed | OK |  | `admin-dashboard/src/app/dashboard/corporate-accounts/[id]/members/page.tsx:235` |
| ☐ | toast | Status unavailable | OK |  | `admin-dashboard/src/app/dashboard/bulk-operations/page.tsx:462` |
| ☐ | toast | Stripe customer emails synced | OK |  | `admin-dashboard/src/app/dashboard/users/page.tsx:312` |
| ☐ | toast | Stripe email sync failed | OK |  | `admin-dashboard/src/app/dashboard/users/page.tsx:324` |
| ☐ | toast | Stripe email sync incomplete | OK |  | `admin-dashboard/src/app/dashboard/users/page.tsx:312` |
| ☐ | toast | Stripe payouts synced | OK |  | `admin-dashboard/src/app/dashboard/drivers/page.tsx:674` |
| ☐ | toast | Subscription assigned | OK |  | `admin-dashboard/src/app/dashboard/corporate-accounts/[id]/subscription/page.tsx:90` |
| ☐ | toast | Subscription cancelled | OK |  | `admin-dashboard/src/app/dashboard/corporate-accounts/[id]/subscription/page.tsx:125` |
| ☐ | toast | Subscription will cancel at period end | OK |  | `admin-dashboard/src/app/dashboard/corporate-accounts/[id]/subscription/page.tsx:125` |
| ☐ | toast | Summary copied | OK |  | `admin-dashboard/src/app/dashboard/riders/legacy-saved-address-backfill/page.tsx:409` |
| ☐ | toast | Summary copied | OK |  | `admin-dashboard/src/app/dashboard/drivers/legacy-import/page.tsx:450` |
| ☐ | toast | Summary copied | OK |  | `admin-dashboard/src/app/dashboard/drivers/legacy-sin-dob-backfill/page.tsx:411` |
| ☐ | toast | Summary copied | OK |  | `admin-dashboard/src/app/dashboard/drivers/import/page.tsx:456` |
| ☐ | toast | Summary copied | OK |  | `admin-dashboard/src/app/dashboard/drivers/legacy-vehicle-history-backfill/page.tsx:402` |
| ☐ | toast | Sync failed | OK |  | `admin-dashboard/src/app/dashboard/support-tickets/page.tsx:89` |
| ☐ | toast | Sync skipped | OK |  | `admin-dashboard/src/app/dashboard/support-tickets/page.tsx:83` |
| ☐ | toast | Synced with errors | OK |  | `admin-dashboard/src/app/dashboard/drivers/page.tsx:674` |
| ☐ | toast | Tag update failed | OK |  | `admin-dashboard/src/app/dashboard/support-tickets/tickets/[id]/page.tsx:323` |
| ☐ | toast | Tags ${label} | OK |  | `admin-dashboard/src/app/dashboard/support-tickets/tickets/[id]/page.tsx:320` |
| ☐ | toast | The AI Assistant card's provider list may be incomplete until you reload. | OK |  | `admin-dashboard/src/app/dashboard/settings/page.tsx:127` |
| ☐ | toast | The CSV has validation errors. Fix them and try again. | OK |  | `admin-dashboard/src/app/dashboard/bulk-operations/page.tsx:523` |
| ☐ | toast | The CSV has validation errors. Fix them and try again. | OK |  | `admin-dashboard/src/app/dashboard/bulk-operations/page.tsx:1513` |
| ☐ | toast | The CSV has validation errors. Fix them and try again. | OK |  | `admin-dashboard/src/app/dashboard/drivers/legacy-import/page.tsx:246` |
| ☐ | toast | The CSV has validation errors. Fix them and try again. | OK |  | `admin-dashboard/src/app/dashboard/drivers/import/page.tsx:253` |
| ☐ | toast | The CSVs have validation errors. Fix them and try again. | OK |  | `admin-dashboard/src/app/dashboard/riders/legacy-saved-address-backfill/page.tsx:218` |
| ☐ | toast | The CSVs have validation errors. Fix them and try again. | OK |  | `admin-dashboard/src/app/dashboard/drivers/legacy-sin-dob-backfill/page.tsx:225` |
| ☐ | toast | The CSVs have validation errors. Fix them and try again. | OK |  | `admin-dashboard/src/app/dashboard/drivers/legacy-vehicle-history-backfill/page.tsx:213` |
| ☐ | toast | The Heat Map Configuration card could not reach the server. Reload to try again. | OK |  | `admin-dashboard/src/app/dashboard/settings/page.tsx:137` |
| ☐ | toast | The Security tab may be showing stale two-factor status. Reload to confirm. | OK |  | `admin-dashboard/src/app/dashboard/settings/page.tsx:157` |
| ☐ | toast | The driver's profile photo was updated and approved. | OK |  | `admin-dashboard/src/app/dashboard/drivers/page.tsx:596` |
| ☐ | toast | The server rejected the update. | OK |  | `admin-dashboard/src/app/dashboard/settings/page.tsx:219` |
| ☐ | toast | The server rejected the update. | OK |  | `admin-dashboard/src/app/dashboard/settings/page.tsx:247` |
| ☐ | toast | The settings page could not reach the server. Try again. | OK |  | `admin-dashboard/src/app/dashboard/settings/page.tsx:106` |
| ☐ | setError | This work email has no company memberships. Ask your company admin for an invite link. | OK |  | `admin-dashboard/src/app/company-login/page.tsx:85` |
| ☐ | toast | Ticket created | OK |  | `admin-dashboard/src/app/dashboard/support-tickets/tickets/page.tsx:190` |
| ☐ | setError | Too many login attempts. Please wait a minute and try again. | OK |  | `admin-dashboard/src/app/login/page.tsx:117` |
| ☐ | toast | Top-up failed | OK |  | `admin-dashboard/src/app/company-portal/[id]/billing/page.tsx:194` |
| ☐ | toast | Top-up submitted | OK |  | `admin-dashboard/src/app/company-portal/[id]/billing/page.tsx:186` |
| ☐ | toast | Two-factor authentication has been removed from your account. | OK |  | `admin-dashboard/src/app/dashboard/settings/page.tsx:175` |
| ☐ | setError | Unknown error | OK |  | `admin-dashboard/src/app/track/[rideId]/page.tsx:104` |
| ☐ | toast | Unknown error | OK |  | `admin-dashboard/src/app/dashboard/stripe-events/page.tsx:167` |
| ☐ | toast | Unknown error | OK |  | `admin-dashboard/src/app/dashboard/stripe-events/page.tsx:187` |
| ☐ | toast | Unknown error | OK |  | `admin-dashboard/src/app/dashboard/users/page.tsx:324` |
| ☐ | toast | Unknown error | OK |  | `admin-dashboard/src/app/dashboard/monitoring/page.tsx:903` |
| ☐ | toast | Unknown error | OK |  | `admin-dashboard/src/app/dashboard/monitoring/page.tsx:946` |
| ☐ | toast | Unknown error | OK |  | `admin-dashboard/src/app/dashboard/monitoring/redis/page.tsx:221` |
| ☐ | toast | Unknown error | OK |  | `admin-dashboard/src/app/dashboard/monitoring/dispatch-geo/page.tsx:115` |
| ☐ | toast | Unknown error | OK |  | `admin-dashboard/src/app/dashboard/bulk-operations/page.tsx:1002` |
| ☐ | toast | Unknown error | OK |  | `admin-dashboard/src/app/dashboard/bulk-operations/page.tsx:1028` |
| ☐ | toast | Unknown error | OK |  | `admin-dashboard/src/app/dashboard/bulk-operations/page.tsx:1182` |
| ☐ | toast | Unknown error | OK |  | `admin-dashboard/src/app/dashboard/bulk-operations/page.tsx:1208` |
| ☐ | toast | Unknown error | OK |  | `admin-dashboard/src/app/dashboard/bulk-operations/page.tsx:1762` |
| ☐ | toast | Unknown error | OK |  | `admin-dashboard/src/app/dashboard/compliance/page.tsx:197` |
| ☐ | toast | Unknown error | OK |  | `admin-dashboard/src/app/dashboard/sentry-logs/page.tsx:244` |
| ☐ | toast | Unknown error | OK |  | `admin-dashboard/src/app/dashboard/sentry-logs/page.tsx:276` |
| ☐ | toast | Unknown error | OK |  | `admin-dashboard/src/app/dashboard/safety/page.tsx:610` |
| ☐ | toast | Unknown error | OK |  | `admin-dashboard/src/app/dashboard/safety/page.tsx:724` |
| ☐ | toast | Unknown error | OK |  | `admin-dashboard/src/app/dashboard/safety/page.tsx:749` |
| ☐ | toast | Unknown error | OK |  | `admin-dashboard/src/app/dashboard/drivers/page.tsx:415` |
| ☐ | toast | Unknown error | OK |  | `admin-dashboard/src/app/dashboard/drivers/page.tsx:429` |
| ☐ | toast | Unknown error | OK |  | `admin-dashboard/src/app/dashboard/drivers/page.tsx:561` |
| ☐ | toast | Unknown error | OK |  | `admin-dashboard/src/app/dashboard/drivers/page.tsx:575` |
| ☐ | toast | Unknown error | OK |  | `admin-dashboard/src/app/dashboard/drivers/page.tsx:598` |
| ☐ | toast | Unknown error | OK |  | `admin-dashboard/src/app/dashboard/drivers/page.tsx:657` |
| ☐ | toast | Unknown error | OK |  | `admin-dashboard/src/app/dashboard/drivers/page.tsx:681` |
| ☐ | toast | Unknown error | OK |  | `admin-dashboard/src/app/dashboard/drivers/page.tsx:724` |
| ☐ | toast | Unknown error | OK |  | `admin-dashboard/src/app/dashboard/drivers/legacy-import/page.tsx:544` |
| ☐ | toast | Unknown error | OK |  | `admin-dashboard/src/app/dashboard/drivers/legacy-import/page.tsx:636` |
| ☐ | toast | Unknown error | OK |  | `admin-dashboard/src/app/dashboard/cloud-messaging/page.tsx:351` |
| ☐ | setError | Update failed | OK |  | `admin-dashboard/src/app/company-portal/[id]/members/page.tsx:141` |
| ☐ | toast | Update failed | OK |  | `admin-dashboard/src/app/dashboard/support-tickets/tickets/[id]/page.tsx:263` |
| ☐ | toast | Update failed | OK |  | `admin-dashboard/src/app/dashboard/bulk-operations/page.tsx:195` |
| ☐ | toast | Update failed | OK |  | `admin-dashboard/src/app/dashboard/vehicle-types/page.tsx:288` |
| ☐ | toast | Update failed | OK |  | `admin-dashboard/src/app/dashboard/safety/page.tsx:749` |
| ☐ | toast | Upload failed | OK |  | `admin-dashboard/src/app/dashboard/vehicle-types/page.tsx:165` |
| ☐ | toast | Upload failed | OK |  | `admin-dashboard/src/app/dashboard/vehicle-types/page.tsx:191` |
| ☐ | toast | Upload failed | OK |  | `admin-dashboard/src/app/dashboard/settings/page.tsx:69` |
| ☐ | setError | Upload failed — please try again. | OK |  | `admin-dashboard/src/app/company-portal/[id]/verification/page.tsx:94` |
| ☐ | toast | User ${change.status === "banned" ? "banned" : "suspended"} | OK |  | `admin-dashboard/src/app/dashboard/users/page.tsx:1181` |
| ☐ | toast | User activated | OK |  | `admin-dashboard/src/app/dashboard/users/page.tsx:755` |
| ☐ | toast | Validation failed | OK |  | `admin-dashboard/src/app/dashboard/bulk-operations/page.tsx:447` |
| ☐ | toast | Validation failed | OK |  | `admin-dashboard/src/app/dashboard/bulk-operations/page.tsx:1465` |
| ☐ | toast | Validation failed | OK |  | `admin-dashboard/src/app/dashboard/riders/legacy-saved-address-backfill/page.tsx:158` |
| ☐ | toast | Validation failed | OK |  | `admin-dashboard/src/app/dashboard/drivers/legacy-import/page.tsx:181` |
| ☐ | toast | Validation failed | OK |  | `admin-dashboard/src/app/dashboard/drivers/legacy-sin-dob-backfill/page.tsx:165` |
| ☐ | toast | Validation failed | OK |  | `admin-dashboard/src/app/dashboard/drivers/import/page.tsx:201` |
| ☐ | toast | Validation failed | OK |  | `admin-dashboard/src/app/dashboard/drivers/legacy-vehicle-history-backfill/page.tsx:154` |
| ☐ | setError | Vehicle must be 9 years old or newer (Saskatchewan regulation). | OK |  | `admin-dashboard/src/app/register/driver/page.tsx:220` |
| ☐ | toast | Vehicle type ${!vt.is_active ? 'activated' : 'deactivated'} | OK |  | `admin-dashboard/src/app/dashboard/vehicle-types/page.tsx:285` |
| ☐ | toast | Vehicle type deleted | OK |  | `admin-dashboard/src/app/dashboard/vehicle-types/page.tsx:267` |
| ☐ | toast | Vehicle type saved | OK |  | `admin-dashboard/src/app/dashboard/vehicle-types/page.tsx:248` |
| ☐ | toast | Wallet ${action === "credit" ? "credited" : "debited"} | OK |  | `admin-dashboard/src/app/dashboard/users/page.tsx:138` |
| ☐ | toast | Welcome letter downloaded | OK |  | `admin-dashboard/src/app/dashboard/drivers/decals/page.tsx:208` |
| ☐ | toast | Welcome letter generated for ${driver.first_name} ${driver.last_name}. | OK |  | `admin-dashboard/src/app/dashboard/drivers/decals/page.tsx:208` |
| ☐ | toast | Welcome letters downloaded | OK |  | `admin-dashboard/src/app/dashboard/drivers/decals/page.tsx:231` |

### `payouts-compliance`  (4)

| ✓ | Trigger | Message the user sees | Verdict | Issue | Source |
|---|---|---|---|---|---|
| ☐ | toast | ${periodLabel} closed | OK |  | `admin-dashboard/src/app/dashboard/earnings/_components/payouts-compliance.tsx:39` |
| ☐ | toast | ${res.payout_count} payouts · ${fmtMoney(res.total_amount)} total | OK |  | `admin-dashboard/src/app/dashboard/earnings/_components/payouts-compliance.tsx:39` |
| ☐ | toast | Close failed | OK |  | `admin-dashboard/src/app/dashboard/earnings/_components/payouts-compliance.tsx:45` |
| ☐ | toast | Unknown error | OK |  | `admin-dashboard/src/app/dashboard/earnings/_components/payouts-compliance.tsx:45` |

### `payouts-tab`  (8)

| ✓ | Trigger | Message the user sees | Verdict | Issue | Source |
|---|---|---|---|---|---|
| ☐ | toast | ${res.retried} queued · ${res.skipped} skipped · ${res.failed_to_initiate} errored. | OK |  | `admin-dashboard/src/app/dashboard/earnings/_components/payouts-tab.tsx:91` |
| ☐ | toast | Bulk retry failed | OK |  | `admin-dashboard/src/app/dashboard/earnings/_components/payouts-tab.tsx:97` |
| ☐ | toast | Bulk retry queued | OK |  | `admin-dashboard/src/app/dashboard/earnings/_components/payouts-tab.tsx:91` |
| ☐ | toast | Payout flipped back to pending — the retry loop will pick it up. | OK |  | `admin-dashboard/src/app/dashboard/earnings/_components/payouts-tab.tsx:70` |
| ☐ | toast | Retry failed | OK |  | `admin-dashboard/src/app/dashboard/earnings/_components/payouts-tab.tsx:73` |
| ☐ | toast | Retry queued | OK |  | `admin-dashboard/src/app/dashboard/earnings/_components/payouts-tab.tsx:70` |
| ☐ | toast | Unknown error | OK |  | `admin-dashboard/src/app/dashboard/earnings/_components/payouts-tab.tsx:73` |
| ☐ | toast | Unknown error | OK |  | `admin-dashboard/src/app/dashboard/earnings/_components/payouts-tab.tsx:97` |

### `pre_launch_flag`  (1)

| ✓ | Trigger | Message the user sees | Verdict | Issue | Source |
|---|---|---|---|---|---|
| ☐ | HTTP 403 | Pre-launch legacy data flagging requires super admin access. | OK |  | `backend/routes/admin/pre_launch_flag.py:56` |

### `promotions`  (4)

| ✓ | Trigger | Message the user sees | Verdict | Issue | Source |
|---|---|---|---|---|---|
| ☐ | HTTP 400 | discount_value cannot be negative | FLAG | code identifier: discount_value | `backend/routes/admin/promotions.py:158` |
| ☐ | HTTP 400 | discount_value cannot exceed $500 for flat discounts | FLAG | code identifier: discount_value | `backend/routes/admin/promotions.py:166` |
| ☐ | HTTP 400 | discount_value cannot exceed 100 for percentage discounts | FLAG | code identifier: discount_value | `backend/routes/admin/promotions.py:161` |
| ☐ | HTTP 400 | {field} cannot be negative | OK |  | `backend/routes/admin/promotions.py:177` |

### `referral-analytics`  (1)

| ✓ | Trigger | Message the user sees | Verdict | Issue | Source |
|---|---|---|---|---|---|
| ☐ | setError | Failed to load referral analytics | OK |  | `admin-dashboard/src/components/referral-analytics.tsx:49` |

### `referral-leaderboard`  (1)

| ✓ | Trigger | Message the user sees | Verdict | Issue | Source |
|---|---|---|---|---|---|
| ☐ | setError | Failed to load referral stats | OK |  | `admin-dashboard/src/components/referral-leaderboard.tsx:26` |

### `retention-cohorts-panel`  (1)

| ✓ | Trigger | Message the user sees | Verdict | Issue | Source |
|---|---|---|---|---|---|
| ☐ | setError | Could not load retention metrics. | OK |  | `admin-dashboard/src/components/analytics/retention-cohorts-panel.tsx:130` |

### `ride-complaint-form`  (2)

| ✓ | Trigger | Message the user sees | Verdict | Issue | Source |
|---|---|---|---|---|---|
| ☐ | toast | Complaint created | OK |  | `admin-dashboard/src/app/dashboard/rides/_components/ride-complaint-form.tsx:41` |
| ☐ | toast | Failed to create complaint | OK |  | `admin-dashboard/src/app/dashboard/rides/_components/ride-complaint-form.tsx:47` |

### `ride-flag-form`  (5)

| ✓ | Trigger | Message the user sees | Verdict | Issue | Source |
|---|---|---|---|---|---|
| ☐ | toast | ${result.active_flag_count} active flags triggered automatic ban. | OK |  | `admin-dashboard/src/app/dashboard/rides/_components/ride-flag-form.tsx:45` |
| ☐ | toast | ${targetType === "rider" ? "Rider" : "Driver"} AUTO-BANNED | OK |  | `admin-dashboard/src/app/dashboard/rides/_components/ride-flag-form.tsx:45` |
| ☐ | toast | Active flags: ${result.active_flag_count}/3 | OK |  | `admin-dashboard/src/app/dashboard/rides/_components/ride-flag-form.tsx:47` |
| ☐ | toast | Failed to flag | OK |  | `admin-dashboard/src/app/dashboard/rides/_components/ride-flag-form.tsx:54` |
| ☐ | toast | Flag added | OK |  | `admin-dashboard/src/app/dashboard/rides/_components/ride-flag-form.tsx:47` |

### `ride-invoice`  (11)

| ✓ | Trigger | Message the user sees | Verdict | Issue | Source |
|---|---|---|---|---|---|
| ☐ | toast | Enter a valid email address. | OK |  | `admin-dashboard/src/app/dashboard/rides/_components/ride-invoice.tsx:53` |
| ☐ | toast | Failed to download invoice | OK |  | `admin-dashboard/src/app/dashboard/rides/_components/ride-invoice.tsx:455` |
| ☐ | toast | Failed to send invoice | OK |  | `admin-dashboard/src/app/dashboard/rides/_components/ride-invoice.tsx:78` |
| ☐ | toast | Failed to send payable invoice | OK |  | `admin-dashboard/src/app/dashboard/rides/_components/ride-invoice.tsx:78` |
| ☐ | toast | Invalid email | OK |  | `admin-dashboard/src/app/dashboard/rides/_components/ride-invoice.tsx:53` |
| ☐ | toast | Invoice sent | OK |  | `admin-dashboard/src/app/dashboard/rides/_components/ride-invoice.tsx:68` |
| ☐ | toast | Payable invoice sent | OK |  | `admin-dashboard/src/app/dashboard/rides/_components/ride-invoice.tsx:60` |
| ☐ | toast | Receipt sent to ${overrideEmail}. | OK |  | `admin-dashboard/src/app/dashboard/rides/_components/ride-invoice.tsx:68` |
| ☐ | toast | Receipt sent to rider's email. | OK |  | `admin-dashboard/src/app/dashboard/rides/_components/ride-invoice.tsx:68` |
| ☐ | toast | Stripe emailed the rider a pay link. | OK |  | `admin-dashboard/src/app/dashboard/rides/_components/ride-invoice.tsx:60` |
| ☐ | toast | Stripe emailed the rider a pay link. The ride settles automatically once paid. | OK |  | `admin-dashboard/src/app/dashboard/rides/_components/ride-invoice.tsx:60` |

### `ride-lost-found`  (4)

| ✓ | Trigger | Message the user sees | Verdict | Issue | Source |
|---|---|---|---|---|---|
| ☐ | toast | Driver has been notified. | OK |  | `admin-dashboard/src/app/dashboard/rides/_components/ride-lost-found.tsx:35` |
| ☐ | toast | Failed to report item | OK |  | `admin-dashboard/src/app/dashboard/rides/_components/ride-lost-found.tsx:40` |
| ☐ | toast | Failed to update item | OK |  | `admin-dashboard/src/app/dashboard/rides/_components/ride-lost-found.tsx:51` |
| ☐ | toast | Lost item reported | OK |  | `admin-dashboard/src/app/dashboard/rides/_components/ride-lost-found.tsx:35` |

### `rider_import`  (4)

| ✓ | Trigger | Message the user sees | Verdict | Issue | Source |
|---|---|---|---|---|---|
| ☐ | HTTP 413 | CSV exceeds the {MAX_CSV_BYTES // 1000} KB limit | OK |  | `backend/routes/admin/rider_import.py:65` |
| ☐ | HTTP 422 | CSV has {len(rows)} rows; the limit is {MAX_ROWS} per import | OK |  | `backend/routes/admin/rider_import.py:75` |
| ☐ | HTTP 422 | CSV must be UTF-8 encoded | OK |  | `backend/routes/admin/rider_import.py:69` |
| ☐ | HTTP 422 | str(e) | OK |  | `backend/routes/admin/rider_import.py:73` |

### `rides`  (40)

| ✓ | Trigger | Message the user sees | Verdict | Issue | Source |
|---|---|---|---|---|---|
| ☐ | HTTP 409 | An invoice is already being created for this ride | OK |  | `backend/routes/admin/rides.py:2117` |
| ☐ | HTTP 409 | An invoice is already being created for this ride | OK |  | `backend/routes/admin/rides.py:2019` |
| ☐ | HTTP 400 | Cannot cancel ride from state '{status_from}' | OK |  | `backend/routes/admin/rides.py:624` |
| ☐ | HTTP 400 | Cannot complete ride from state '{ride.get('status')}' | OK |  | `backend/routes/admin/rides.py:844` |
| ☐ | HTTP 409 | Cannot retry a payout in status '{payout.get('status')}' — only failed or cancelled payouts can be retried | OK |  | `backend/routes/admin/rides.py:3732` |
| ☐ | HTTP 403 | Full licence export requires super-admin access. | OK |  | `backend/routes/admin/rides.py:3035` |
| ☐ | HTTP 409 | Invoice already paid — webhook will settle the ride | OK |  | `backend/routes/admin/rides.py:2058` |
| ☐ | HTTP 409 | Invoice claim changed during creation | OK |  | `backend/routes/admin/rides.py:2186` |
| ☐ | HTTP 409 | Invoice is only for completed rides; ride is '{ride.get('status')}' | OK |  | `backend/routes/admin/rides.py:1924` |
| ☐ | HTTP 400 | Pass either payout_ids[] OR since (and optional service_area_id), not both. | FLAG | code identifier: payout_ids, service_area_id | `backend/routes/admin/rides.py:3794` |
| ☐ | HTTP 409 | Payable invoices are only for card rides; this ride uses '{_pmethod}'. Use the corporate/wallet remediation path. | OK |  | `backend/routes/admin/rides.py:1955` |
| ☐ | HTTP 409 | Payment is currently processing — try again in a moment | OK |  | `backend/routes/admin/rides.py:1947` |
| ☐ | HTTP 404 | Payout not found | OK |  | `backend/routes/admin/rides.py:3695` |
| ☐ | HTTP 404 | Payout not found | OK |  | `backend/routes/admin/rides.py:3730` |
| ☐ | HTTP 409 | Ride has an open pre-authorization hold pending capture — cannot invoice yet | OK |  | `backend/routes/admin/rides.py:1967` |
| ☐ | HTTP 409 | Ride is in terminal payment state '{ride.get('payment_status')}' — cannot invoice | OK |  | `backend/routes/admin/rides.py:1940` |
| ☐ | HTTP 400 | Ride is missing coordinates | OK |  | `backend/routes/admin/rides.py:2304` |
| ☐ | HTTP 400 | Ride is not held for review (payment_status='{ride.get('payment_status')}') | FLAG | code identifier: payment_status | `backend/routes/admin/rides.py:537` |
| ☐ | HTTP 400 | Ride is not held for review (payment_status='{ride.get('payment_status')}') | FLAG | code identifier: payment_status | `backend/routes/admin/rides.py:571` |
| ☐ | HTTP 404 | Ride not found | OK |  | `backend/routes/admin/rides.py:535` |
| ☐ | HTTP 404 | Ride not found | OK |  | `backend/routes/admin/rides.py:569` |
| ☐ | HTTP 404 | Ride not found | OK |  | `backend/routes/admin/rides.py:620` |
| ☐ | HTTP 404 | Ride not found | OK |  | `backend/routes/admin/rides.py:841` |
| ☐ | HTTP 404 | Ride not found | OK |  | `backend/routes/admin/rides.py:1737` |
| ☐ | HTTP 404 | Ride not found | OK |  | `backend/routes/admin/rides.py:1755` |
| ☐ | HTTP 404 | Ride not found | OK |  | `backend/routes/admin/rides.py:1764` |
| ☐ | HTTP 404 | Ride not found | OK |  | `backend/routes/admin/rides.py:1861` |
| ☐ | HTTP 404 | Ride not found | OK |  | `backend/routes/admin/rides.py:1921` |
| ☐ | HTTP 404 | Ride not found | OK |  | `backend/routes/admin/rides.py:2380` |
| ☐ | HTTP 409 | Ride state changed before the cancel could be applied — refresh and retry | OK |  | `backend/routes/admin/rides.py:720` |
| ☐ | HTTP 409 | Ride state changed before the completion could be applied — refresh and retry | OK |  | `backend/routes/admin/rides.py:898` |
| ☐ | HTTP 409 | Ride total is $0 — nothing to invoice | OK |  | `backend/routes/admin/rides.py:1992` |
| ☐ | HTTP 422 | Rider has no email address on file | OK |  | `backend/routes/admin/rides.py:1975` |
| ☐ | HTTP 422 | Rider has no email address on file. Provide an email to send the receipt to. | OK |  | `backend/routes/admin/rides.py:1869` |
| ☐ | HTTP 403 | This action requires super admin access. | OK |  | `backend/routes/admin/rides.py:4000` |
| ☐ | HTTP 403 | This action requires super admin access. | OK |  | `backend/routes/admin/rides.py:4228` |
| ☐ | HTTP 403 | This action requires the finance role. | OK |  | `backend/routes/admin/rides.py:3726` |
| ☐ | HTTP 403 | This action requires the finance role. | OK |  | `backend/routes/admin/rides.py:3791` |
| ☐ | HTTP 403 | This action requires the finance role. | OK |  | `backend/routes/admin/rides.py:3913` |
| ☐ | HTTP 413 | Too many matching drivers. Narrow the filters and export again. | OK |  | `backend/routes/admin/rides.py:3105` |

### `safety`  (5)

| ✓ | Trigger | Message the user sees | Verdict | Issue | Source |
|---|---|---|---|---|---|
| ☐ | HTTP 422 | Cannot merge an incident into itself. | OK |  | `backend/routes/admin/safety.py:415` |
| ☐ | HTTP 404 | Canonical incident not found | OK |  | `backend/routes/admin/safety.py:423` |
| ☐ | HTTP 404 | Incident not found | OK |  | `backend/routes/admin/safety.py:170` |
| ☐ | HTTP 404 | Incident not found | OK |  | `backend/routes/admin/safety.py:343` |
| ☐ | HTTP 404 | Incident not found | OK |  | `backend/routes/admin/safety.py:419` |

### `scheduled-rides-tab`  (2)

| ✓ | Trigger | Message the user sees | Verdict | Issue | Source |
|---|---|---|---|---|---|
| ☐ | setError | Could not save scheduled ride settings. Your changes are still here; please try again. | OK |  | `admin-dashboard/src/app/dashboard/service-areas/_components/scheduled-rides-tab.tsx:45` |
| ☐ | setError | Enter whole minutes: matching 0–30; driver and rider reminders 1–60. | OK |  | `admin-dashboard/src/app/dashboard/service-areas/_components/scheduled-rides-tab.tsx:33` |

### `sentry`  (9)

| ✓ | Trigger | Message the user sees | Verdict | Issue | Source |
|---|---|---|---|---|---|
| ☐ | HTTP 400 | Invalid Sentry issue id | OK |  | `backend/routes/admin/sentry.py:300` |
| ☐ | HTTP 404 | Sentry issue is not in a configured Spinr project | OK |  | `backend/routes/admin/sentry.py:731` |
| ☐ | HTTP 404 | Sentry issue is not in a configured Spinr project | OK |  | `backend/routes/admin/sentry.py:721` |
| ☐ | HTTP 404 | Sentry issue or project not found | OK |  | `backend/routes/admin/sentry.py:341` |
| ☐ | HTTP 400 | Surface '{surface}' is not configured | OK |  | `backend/routes/admin/sentry.py:254` |
| ☐ | HTTP 400 | Surface '{surface}' is not configured | OK |  | `backend/routes/admin/sentry.py:244` |
| ☐ | HTTP 400 | stats_period must look like 24h, 7d, 30d, or 12w | FLAG | code identifier: stats_period | `backend/routes/admin/sentry.py:277` |
| ☐ | HTTP 400 | status must be one of: {_STATUSES_HELP} | OK |  | `backend/routes/admin/sentry.py:568` |
| ☐ | HTTP 400 | status must be one of: {_STATUSES_HELP} | OK |  | `backend/routes/admin/sentry.py:819` |

### `service_areas`  (7)

| ✓ | Trigger | Message the user sees | Verdict | Issue | Source |
|---|---|---|---|---|---|
| ☐ | HTTP 400 | Airport polygon is too large (bbox {bbox['lat_km']} km × {bbox['lng_km']} km, max {_AIRPORT_MAX_BBOX_KM} km per side). Either redraw the polygon to cover only the actual airport property, or uncheck 'is_airport' on this  | FLAG | code identifier: is_airport | `backend/routes/admin/service_areas.py:125` |
| ☐ | HTTP 400 | Changing GST/PST/HST configuration requires a written justification (regulatory + financial risk). | OK |  | `backend/routes/admin/service_areas.py:763` |
| ☐ | HTTP 400 | Changing GST/PST/HST configuration requires a written justification (regulatory + financial risk). | OK |  | `backend/routes/admin/service_areas.py:1279` |
| ☐ | HTTP 404 | Service area not found | OK |  | `backend/routes/admin/service_areas.py:462` |
| ☐ | HTTP 404 | Service area not found | OK |  | `backend/routes/admin/service_areas.py:1273` |
| ☐ | HTTP 400 | is_airport may only be set on a sub-region (a service area with a parent_service_area_id). Top-level service areas cannot be airport zones — create the airport as a child row under its parent service area instead. | FLAG | code identifier: is_airport, parent_service_area_id | `backend/routes/admin/service_areas.py:153` |
| ☐ | HTTP 400 | surge_multiplier above 2.5 requires a written justification (regulatory + reputational risk) | FLAG | code identifier: surge_multiplier | `backend/routes/admin/service_areas.py:726` |

### `settings`  (5)

| ✓ | Trigger | Message the user sees | Verdict | Issue | Source |
|---|---|---|---|---|---|
| ☐ | HTTP 400 | Field '{field}' is not a revealable credential | OK |  | `backend/routes/admin/settings.py:714` |
| ☐ | HTTP 400 | File exceeds 500 KB limit | OK |  | `backend/routes/admin/settings.py:834` |
| ☐ | HTTP 403 | Only super admins can change {field} | OK |  | `backend/routes/admin/settings.py:772` |
| ☐ | HTTP 403 | Only super admins can reveal credential values | OK |  | `backend/routes/admin/settings.py:712` |
| ☐ | HTTP 400 | Unsupported content-type {content_type}. Accepted: {sorted(_SOUND_MIME_TYPES)} | OK |  | `backend/routes/admin/settings.py:827` |

### `sgi_forms`  (14)

| ✓ | Trigger | Message the user sees | Verdict | Issue | Source |
|---|---|---|---|---|---|
| ☐ | HTTP 400 | No drivers selected | OK |  | `backend/routes/admin/sgi_forms.py:140` |
| ☐ | HTTP 400 | No drivers selected | OK |  | `backend/routes/admin/sgi_forms.py:348` |
| ☐ | HTTP 400 | No drivers selected | OK |  | `backend/routes/admin/sgi_forms.py:519` |
| ☐ | HTTP 404 | None of the requested drivers could be found | OK |  | `backend/routes/admin/sgi_forms.py:157` |
| ☐ | HTTP 404 | None of the requested drivers could be found | OK |  | `backend/routes/admin/sgi_forms.py:360` |
| ☐ | HTTP 404 | None of the requested drivers could be found | OK |  | `backend/routes/admin/sgi_forms.py:394` |
| ☐ | HTTP 404 | None of the requested drivers could be found | OK |  | `backend/routes/admin/sgi_forms.py:531` |
| ☐ | HTTP 403 | SGI compliance forms require super admin access. | OK |  | `backend/routes/admin/sgi_forms.py:71` |
| ☐ | HTTP 422 | {len(body.driver_ids)} drivers requested; the document bundle is limited to {MAX_DOCUMENT_BUNDLE_DRIVERS} per download. Split the selection into smaller batches. | OK |  | `backend/routes/admin/sgi_forms.py:350` |
| ☐ | HTTP 422 | {len(body.driver_ids)} drivers requested; the submission package is limited to {MAX_DOCUMENT_BUNDLE_DRIVERS} per download. Split the selection into smaller batches. | OK |  | `backend/routes/admin/sgi_forms.py:521` |
| ☐ | HTTP 422 | {len(body.driver_ids)} drivers requested; the {body.form_type} form has {max_rows} rows | OK |  | `backend/routes/admin/sgi_forms.py:142` |
| ☐ | HTTP 422 | {len(out_of_scope)} of the selected drivers are regulated by {', '.join(authorities)}, not SGI — SGI forms cannot be generated for them. Remove these drivers from the selection and generate their own province's form sepa | OK |  | `backend/routes/admin/sgi_forms.py:162` |
| ☐ | HTTP 422 | {len(out_of_scope)} of the selected drivers are regulated by {', '.join(authorities)}, not SGI — their documents cannot be bundled into an SGI submission. Remove these drivers from the selection. | OK |  | `backend/routes/admin/sgi_forms.py:367` |
| ☐ | HTTP 422 | {len(out_of_scope)} of the selected drivers are regulated by {', '.join(authorities)}, not SGI — they cannot be included in an SGI submission. Remove these drivers from the selection. | OK |  | `backend/routes/admin/sgi_forms.py:536` |

### `spinr-pass-area-tab`  (2)

| ✓ | Trigger | Message the user sees | Verdict | Issue | Source |
|---|---|---|---|---|---|
| ☐ | toast | Failed to delete plan | OK |  | `admin-dashboard/src/app/dashboard/service-areas/_components/spinr-pass-area-tab.tsx:70` |
| ☐ | toast | Failed to save plan | OK |  | `admin-dashboard/src/app/dashboard/service-areas/_components/spinr-pass-area-tab.tsx:54` |

### `staff`  (14)

| ✓ | Trigger | Message the user sees | Verdict | Issue | Source |
|---|---|---|---|---|---|
| ☐ | HTTP 401 | Actor not found | OK |  | `backend/routes/admin/staff.py:162` |
| ☐ | HTTP 400 | Cannot delete your own account | OK |  | `backend/routes/admin/staff.py:461` |
| ☐ | HTTP 400 | Cannot demote the last active super admin | OK |  | `backend/routes/admin/staff.py:321` |
| ☐ | HTTP 400 | Email already registered as staff | OK |  | `backend/routes/admin/staff.py:221` |
| ☐ | HTTP 401 | Incorrect password — request denied | OK |  | `backend/routes/admin/staff.py:165` |
| ☐ | HTTP 400 | MFA is not enabled for this staff member | OK |  | `backend/routes/admin/staff.py:418` |
| ☐ | HTTP 403 | Only super admins can update staff members | OK |  | `backend/routes/admin/staff.py:308` |
| ☐ | HTTP 400 | Password is required. | OK |  | `backend/routes/admin/staff.py:212` |
| ☐ | HTTP 404 | Staff member not found | OK |  | `backend/routes/admin/staff.py:297` |
| ☐ | HTTP 404 | Staff member not found | OK |  | `backend/routes/admin/staff.py:311` |
| ☐ | HTTP 404 | Staff member not found | OK |  | `backend/routes/admin/staff.py:416` |
| ☐ | HTTP 403 | This action requires the {role.replace('_', ' ')} role. | OK |  | `backend/routes/admin/staff.py:32` |
| ☐ | HTTP 400 | Use Settings → Disable MFA (or a backup code at login) for your own account | OK |  | `backend/routes/admin/staff.py:410` |
| ☐ | HTTP 422 | password_confirmation required for {reason} | FLAG | code identifier: password_confirmation | `backend/routes/admin/staff.py:155` |

### `stripe_connect_ledger`  (1)

| ✓ | Trigger | Message the user sees | Verdict | Issue | Source |
|---|---|---|---|---|---|
| ☐ | HTTP 403 | Connect ledger sync requires super admin access. | OK |  | `backend/routes/admin/stripe_connect_ledger.py:53` |

### `stripe_events`  (7)

| ✓ | Trigger | Message the user sees | Verdict | Issue | Source |
|---|---|---|---|---|---|
| ☐ | HTTP 400 | Body must contain {"confirm": "REPLAY"} | OK |  | `backend/routes/admin/stripe_events.py:200` |
| ☐ | HTTP 409 | Event already processed | OK |  | `backend/routes/admin/stripe_events.py:283` |
| ☐ | HTTP 409 | Event already processed — cannot replay | OK |  | `backend/routes/admin/stripe_events.py:206` |
| ☐ | HTTP 404 | Event not found | OK |  | `backend/routes/admin/stripe_events.py:165` |
| ☐ | HTTP 404 | Event not found | OK |  | `backend/routes/admin/stripe_events.py:204` |
| ☐ | HTTP 404 | Event not found | OK |  | `backend/routes/admin/stripe_events.py:281` |
| ☐ | HTTP 409 | Event was re-claimed by another process between unclaim and replay | OK |  | `backend/routes/admin/stripe_events.py:223` |

### `stripe_import`  (5)

| ✓ | Trigger | Message the user sees | Verdict | Issue | Source |
|---|---|---|---|---|---|
| ☐ | HTTP 413 | CSV exceeds the {MAX_CSV_BYTES // 1000} KB limit | OK |  | `backend/routes/admin/stripe_import.py:79` |
| ☐ | HTTP 422 | CSV has {len(rows)} rows; the limit is {MAX_ROWS} per import | OK |  | `backend/routes/admin/stripe_import.py:89` |
| ☐ | HTTP 422 | CSV must be UTF-8 encoded | OK |  | `backend/routes/admin/stripe_import.py:83` |
| ☐ | HTTP 403 | Stripe mapping import requires super admin access. | OK |  | `backend/routes/admin/stripe_import.py:69` |
| ☐ | HTTP 422 | str(e) | OK |  | `backend/routes/admin/stripe_import.py:87` |

### `stripe_mode_audit`  (1)

| ✓ | Trigger | Message the user sees | Verdict | Issue | Source |
|---|---|---|---|---|---|
| ☐ | HTTP 403 | Super admin access required | OK |  | `backend/routes/admin/stripe_mode_audit.py:95` |

### `stripe_payout_sync`  (1)

| ✓ | Trigger | Message the user sees | Verdict | Issue | Source |
|---|---|---|---|---|---|
| ☐ | HTTP 403 | Stripe payout sync requires super admin access. | OK |  | `backend/routes/admin/stripe_payout_sync.py:54` |

### `subscriptions`  (6)

| ✓ | Trigger | Message the user sees | Verdict | Issue | Source |
|---|---|---|---|---|---|
| ☐ | HTTP 404 | Invoice could not be generated | OK |  | `backend/routes/admin/subscriptions.py:668` |
| ☐ | HTTP 404 | Invoice could not be generated | OK |  | `backend/routes/admin/subscriptions.py:711` |
| ☐ | HTTP 429 | Invoice was just resent — please wait a minute before retrying. | OK |  | `backend/routes/admin/subscriptions.py:737` |
| ☐ | HTTP 404 | Payment not found | OK |  | `backend/routes/admin/subscriptions.py:664` |
| ☐ | HTTP 404 | Payment not found | OK |  | `backend/routes/admin/subscriptions.py:707` |
| ☐ | HTTP 404 | Service area not found | OK |  | `backend/routes/admin/subscriptions.py:436` |

### `supply-panel`  (1)

| ✓ | Trigger | Message the user sees | Verdict | Issue | Source |
|---|---|---|---|---|---|
| ☐ | setError | Could not load supply metrics. | OK |  | `admin-dashboard/src/components/analytics/supply-panel.tsx:41` |

### `support`  (10)

| ✓ | Trigger | Message the user sees | Verdict | Issue | Source |
|---|---|---|---|---|---|
| ☐ | HTTP 404 | Complaint not found | OK |  | `backend/routes/admin/support.py:632` |
| ☐ | HTTP 404 | Dispute not found | OK |  | `backend/routes/admin/support.py:294` |
| ☐ | HTTP 404 | Flag not found | OK |  | `backend/routes/admin/support.py:566` |
| ☐ | HTTP 400 | No {req.against_type} assigned to this ride | OK |  | `backend/routes/admin/support.py:594` |
| ☐ | HTTP 400 | No {req.target_type} assigned to this ride | OK |  | `backend/routes/admin/support.py:512` |
| ☐ | HTTP 404 | Ride not found | OK |  | `backend/routes/admin/support.py:505` |
| ☐ | HTTP 404 | Ride not found | OK |  | `backend/routes/admin/support.py:587` |
| ☐ | HTTP 404 | Ticket not found | OK |  | `backend/routes/admin/support.py:414` |
| ☐ | HTTP 400 | against_type must be 'rider' or 'driver' | FLAG | code identifier: against_type | `backend/routes/admin/support.py:590` |
| ☐ | HTTP 400 | target_type must be 'rider' or 'driver' | FLAG | code identifier: target_type | `backend/routes/admin/support.py:508` |

### `support_tickets`  (5)

| ✓ | Trigger | Message the user sees | Verdict | Issue | Source |
|---|---|---|---|---|---|
| ☐ | HTTP 400 | No fields supplied | OK |  | `backend/routes/admin/support_tickets.py:216` |
| ☐ | HTTP 400 | No fields supplied | OK |  | `backend/routes/admin/support_tickets.py:690` |
| ☐ | HTTP 400 | No reply-from email configured. Set a default reply-from address in Help Desk settings. | OK |  | `backend/routes/admin/support_tickets.py:628` |
| ☐ | HTTP 400 | No tag changes supplied | OK |  | `backend/routes/admin/support_tickets.py:711` |
| ☐ | HTTP 404 | Service area not found | OK |  | `backend/routes/admin/support_tickets.py:780` |

### `surge-history-chart`  (2)

| ✓ | Trigger | Message the user sees | Verdict | Issue | Source |
|---|---|---|---|---|---|
| ☐ | setError | Couldn't load surge history. | OK |  | `admin-dashboard/src/app/dashboard/service-areas/_components/surge-history-chart.tsx:50` |
| ☐ | setError | No Analytics module access — surge history unavailable. | OK |  | `admin-dashboard/src/app/dashboard/service-areas/_components/surge-history-chart.tsx:50` |

### `tax_id_import`  (11)

| ✓ | Trigger | Message the user sees | Verdict | Issue | Source |
|---|---|---|---|---|---|
| ☐ | HTTP 413 | CSV exceeds the {MAX_CSV_BYTES // 1000} KB limit | OK |  | `backend/routes/admin/tax_id_import.py:133` |
| ☐ | HTTP 413 | CSV exceeds the {MAX_LEGACY_EXPORT_CSV_BYTES // 1000000} MB limit | OK |  | `backend/routes/admin/tax_id_import.py:271` |
| ☐ | HTTP 422 | CSV has {len(rows)} rows; the limit is {MAX_LEGACY_EXPORT_ROWS} per import | OK |  | `backend/routes/admin/tax_id_import.py:283` |
| ☐ | HTTP 422 | CSV has {len(rows)} rows; the limit is {MAX_ROWS} per import | OK |  | `backend/routes/admin/tax_id_import.py:143` |
| ☐ | HTTP 422 | CSV must be UTF-8 encoded | OK |  | `backend/routes/admin/tax_id_import.py:137` |
| ☐ | HTTP 422 | CSV must be UTF-8 encoded | OK |  | `backend/routes/admin/tax_id_import.py:277` |
| ☐ | HTTP 422 | No rows with a SIN or GST/HST BN matched a driver by phone | OK |  | `backend/routes/admin/tax_id_import.py:472` |
| ☐ | HTTP 403 | Tax ID import requires super admin access. | OK |  | `backend/routes/admin/tax_id_import.py:108` |
| ☐ | HTTP 400 | Validate these CSVs before committing (or re-validate — a file or batch changed): {e} | OK |  | `backend/routes/admin/tax_id_import.py:514` |
| ☐ | HTTP 422 | str(e) | OK |  | `backend/routes/admin/tax_id_import.py:141` |
| ☐ | HTTP 422 | str(e) | OK |  | `backend/routes/admin/tax_id_import.py:281` |

### `tickets`  (4)

| ✓ | Trigger | Message the user sees | Verdict | Issue | Source |
|---|---|---|---|---|---|
| ☐ | toast | Failed to save ticket | OK |  | `admin-dashboard/src/app/dashboard/support/_tabs/tickets.tsx:113` |
| ☐ | toast | Missing subject | OK |  | `admin-dashboard/src/app/dashboard/support/_tabs/tickets.tsx:106` |
| ☐ | toast | Ticket created | OK |  | `admin-dashboard/src/app/dashboard/support/_tabs/tickets.tsx:111` |
| ☐ | toast | Ticket updated | OK |  | `admin-dashboard/src/app/dashboard/support/_tabs/tickets.tsx:111` |

### `use-crud-toast`  (7)

| ✓ | Trigger | Message the user sees | Verdict | Issue | Source |
|---|---|---|---|---|---|
| ☐ | toast | ${entity} added successfully. | OK |  | `admin-dashboard/src/components/ui/use-crud-toast.ts:23` |
| ☐ | toast | ${entity} created | OK |  | `admin-dashboard/src/components/ui/use-crud-toast.ts:23` |
| ☐ | toast | ${entity} deleted | OK |  | `admin-dashboard/src/components/ui/use-crud-toast.ts:37` |
| ☐ | toast | ${entity} removed. | OK |  | `admin-dashboard/src/components/ui/use-crud-toast.ts:37` |
| ☐ | toast | ${entity} saved. | OK |  | `admin-dashboard/src/components/ui/use-crud-toast.ts:30` |
| ☐ | toast | ${entity} updated | OK |  | `admin-dashboard/src/components/ui/use-crud-toast.ts:30` |
| ☐ | toast | Failed to ${verb} | OK |  | `admin-dashboard/src/components/ui/use-crud-toast.ts:49` |

### `users`  (8)

| ✓ | Trigger | Message the user sees | Verdict | Issue | Source |
|---|---|---|---|---|---|
| ☐ | HTTP 422 | A reason is required to {verb} an account. | OK |  | `backend/routes/admin/users.py:288` |
| ☐ | HTTP 404 | DSAR request not found | OK |  | `backend/routes/admin/users.py:556` |
| ☐ | HTTP 400 | Provide at least one of is_rider or is_driver | FLAG | code identifier: is_driver, is_rider | `backend/routes/admin/users.py:391` |
| ☐ | HTTP 404 | User not found | OK |  | `backend/routes/admin/users.py:235` |
| ☐ | HTTP 404 | User not found | OK |  | `backend/routes/admin/users.py:284` |
| ☐ | HTTP 404 | User not found | OK |  | `backend/routes/admin/users.py:395` |
| ☐ | HTTP 400 | role must be one of rider, driver, both, admin, all | OK |  | `backend/routes/admin/users.py:102` |
| ☐ | HTTP 400 | status must be one of in_progress, completed, rejected | FLAG | code identifier: in_progress | `backend/routes/admin/users.py:549` |

### `vehicle_fleet`  (19)

| ✓ | Trigger | Message the user sees | Verdict | Issue | Source |
|---|---|---|---|---|---|
| ☐ | HTTP 400 | Could not decode image file | OK |  | `backend/routes/admin/vehicle_fleet.py:81` |
| ☐ | HTTP 400 | File exceeds 500 KB limit | OK |  | `backend/routes/admin/vehicle_fleet.py:269` |
| ☐ | HTTP 400 | File exceeds 500 KB limit | OK |  | `backend/routes/admin/vehicle_fleet.py:342` |
| ☐ | HTTP 400 | Image has an opaque background. {fix_hint} | OK |  | `backend/routes/admin/vehicle_fleet.py:85` |
| ☐ | HTTP 422 | Invalid marker_variant '{value}'. Must be one of: {', '.join(sorted(_VALID_MARKER_VARIANTS))} | FLAG | code identifier: marker_variant | `backend/routes/admin/vehicle_fleet.py:50` |
| ☐ | HTTP 422 | Invalid status. Must be one of: {', '.join(sorted(_VALID_STATUSES))} | OK |  | `backend/routes/admin/vehicle_fleet.py:657` |
| ☐ | HTTP 404 | Item not found | OK |  | `backend/routes/admin/vehicle_fleet.py:607` |
| ☐ | HTTP 404 | Item not found | OK |  | `backend/routes/admin/vehicle_fleet.py:671` |
| ☐ | HTTP 404 | Item not found | OK |  | `backend/routes/admin/vehicle_fleet.py:687` |
| ☐ | HTTP 400 | JPEG cannot store transparency. {fix_hint} | OK |  | `backend/routes/admin/vehicle_fleet.py:76` |
| ☐ | HTTP 400 | Markers must be transparent PNG or WebP (JPEG has no alpha channel) | OK |  | `backend/routes/admin/vehicle_fleet.py:335` |
| ☐ | HTTP 400 | No driver assigned to this ride | OK |  | `backend/routes/admin/vehicle_fleet.py:536` |
| ☐ | HTTP 404 | Ride not found | OK |  | `backend/routes/admin/vehicle_fleet.py:531` |
| ☐ | HTTP 400 | Unsupported content-type {content_type}. Accepted: {sorted(_ILLUSTRATION_MIME_TYPES)} | OK |  | `backend/routes/admin/vehicle_fleet.py:262` |
| ☐ | HTTP 404 | Vehicle type not found | OK |  | `backend/routes/admin/vehicle_fleet.py:256` |
| ☐ | HTTP 404 | Vehicle type not found | OK |  | `backend/routes/admin/vehicle_fleet.py:331` |
| ☐ | HTTP 404 | Vehicle type not found | OK |  | `backend/routes/admin/vehicle_fleet.py:401` |
| ☐ | HTTP 409 | f"Cannot delete '{vt_name}' — still used by " + ' and '.join(parts) + '. Remove those references first in Service Areas → Vehicle Pricing.' | OK |  | `backend/routes/admin/vehicle_fleet.py:431` |
| ☐ | HTTP 422 | status must be 'resolved' or 'unresolved' | OK |  | `backend/routes/admin/vehicle_fleet.py:595` |

### `venues`  (2)

| ✓ | Trigger | Message the user sees | Verdict | Issue | Source |
|---|---|---|---|---|---|
| ☐ | HTTP 404 | Venue not found | OK |  | `backend/routes/admin/venues.py:92` |
| ☐ | HTTP 404 | Venue not found | OK |  | `backend/routes/admin/venues.py:106` |

### `wallet`  (6)

| ✓ | Trigger | Message the user sees | Verdict | Issue | Source |
|---|---|---|---|---|---|
| ☐ | HTTP 400 | Insufficient balance. Need ${debit}, have ${old_balance} | OK |  | `backend/routes/admin/wallet.py:261` |
| ☐ | HTTP 400 | Insufficient balance. Need ${debit}, have ${old_balance} | OK |  | `backend/routes/admin/wallet.py:284` |
| ☐ | HTTP 404 | User not found | OK |  | `backend/routes/admin/wallet.py:87` |
| ☐ | HTTP 404 | User not found | OK |  | `backend/routes/admin/wallet.py:153` |
| ☐ | HTTP 404 | User not found | OK |  | `backend/routes/admin/wallet.py:255` |
| ☐ | HTTP 403 | Wallet is suspended | OK |  | `backend/routes/admin/wallet.py:157` |

### `wallet_import`  (6)

| ✓ | Trigger | Message the user sees | Verdict | Issue | Source |
|---|---|---|---|---|---|
| ☐ | HTTP 403 | Legacy wallet import requires super admin access. | OK |  | `backend/routes/admin/wallet_import.py:122` |
| ☐ | HTTP 413 | {label} CSV exceeds the {MAX_CSV_BYTES // 1000000} MB limit | OK |  | `backend/routes/admin/wallet_import.py:92` |
| ☐ | HTTP 422 | {label} CSV has {len(rows)} rows; the limit is {MAX_ROWS} per import | OK |  | `backend/routes/admin/wallet_import.py:107` |
| ☐ | HTTP 422 | {label} CSV is empty | OK |  | `backend/routes/admin/wallet_import.py:97` |
| ☐ | HTTP 422 | {label} CSV must be UTF-8 encoded | OK |  | `backend/routes/admin/wallet_import.py:101` |
| ☐ | HTTP 422 | {label} CSV: {e} | OK |  | `backend/routes/admin/wallet_import.py:105` |

### `zoho-config-card`  (6)

| ✓ | Trigger | Message the user sees | Verdict | Issue | Source |
|---|---|---|---|---|---|
| ☐ | toast | ${r.departments?.length ?? 0} department(s) reachable | OK |  | `admin-dashboard/src/app/dashboard/support-tickets/_components/zoho-config-card.tsx:122` |
| ☐ | toast | Connection OK | OK |  | `admin-dashboard/src/app/dashboard/support-tickets/_components/zoho-config-card.tsx:122` |
| ☐ | toast | Connection failed | OK |  | `admin-dashboard/src/app/dashboard/support-tickets/_components/zoho-config-card.tsx:127` |
| ☐ | toast | Failed to load Zoho config | OK |  | `admin-dashboard/src/app/dashboard/support-tickets/_components/zoho-config-card.tsx:74` |
| ☐ | toast | Save failed | OK |  | `admin-dashboard/src/app/dashboard/support-tickets/_components/zoho-config-card.tsx:112` |
| ☐ | toast | Zoho Desk configuration saved | OK |  | `admin-dashboard/src/app/dashboard/support-tickets/_components/zoho-config-card.tsx:109` |

---
