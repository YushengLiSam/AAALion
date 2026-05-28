import Foundation

/// One "该补货啦" entry returned by `GET /repurchase/reminders`.
///
/// Backend lives in `server/app/services/repurchase_db.py` + the route
/// in `server/app/routes/repurchase.py`. Sam shipped both in Yusheng
/// branch commit `7f15720`. Full design: `docs/REPURCHASE_PLAN.md`.
///
/// JSON shape (decoded):
/// ```
/// {
///   "id": 42,
///   "product_id": "p_1_real_03",
///   "product": { ... full ProductCard ... },
///   "purchased_at": 1745692800,
///   "next_due_at": 1750876800,
///   "days_overdue": 3,
///   "reminder_text": "你购买的「...」已经到周期了,要不要再来一单?"
/// }
/// ```
struct RepurchaseReminder: Codable, Identifiable, Hashable {
    let rowId: Int          // server-side purchases.id; used as Identifiable
    let productId: String
    let product: ProductCard
    let purchasedAt: Int
    let nextDueAt: Int
    let daysOverdue: Int
    let reminderText: String

    var id: Int { rowId }

    enum CodingKeys: String, CodingKey {
        case rowId = "id"
        case productId = "product_id"
        case product
        case purchasedAt = "purchased_at"
        case nextDueAt = "next_due_at"
        case daysOverdue = "days_overdue"
        case reminderText = "reminder_text"
    }
}

/// Response envelope: `{"reminders": [...]}`. Empty list when nothing
/// is due — this is the happy-path return for new users / quiet users,
/// not an error.
struct RemindersResponse: Codable {
    let reminders: [RepurchaseReminder]
}

/// Response from `POST /repurchase/purchase`.
struct PurchaseRecordResponse: Codable {
    let id: Int
    let nextDueAt: Int

    enum CodingKeys: String, CodingKey {
        case id
        case nextDueAt = "next_due_at"
    }
}
