import RallyKit
import SwiftUI

extension Binding where Value == String? {
    /// A `YYYY-MM-DD` string field as a `Date` a `DatePicker` can drive. Reading
    /// an unset field gives `fallback`; writing always sets the string.
    func day(fallback: Date = .now) -> Binding<Date> {
        Binding<Date>(
            get: { DayString.date(wrappedValue) ?? fallback },
            set: { wrappedValue = DayString.string($0) })
    }

    /// An on/off switch over an optional: on sets it to `fallback`, off clears it.
    func isPresent(fallback: Date = .now) -> Binding<Bool> {
        Binding<Bool>(
            get: { wrappedValue != nil },
            set: { wrappedValue = $0 ? (wrappedValue ?? DayString.string(fallback)) : nil })
    }
}

extension Binding where Value == String {
    /// A non-optional `YYYY-MM-DD` field viewed as the optional the `day()` helper expects.
    var nonOptional: Binding<String?> {
        Binding<String?>(get: { wrappedValue }, set: { wrappedValue = $0 ?? wrappedValue })
    }
}
