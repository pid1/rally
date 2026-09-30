import Foundation
import Observation

public enum NoteLogic {
    /// "Today", "Tomorrow", or the weekday and date, with the date always
    /// present: a planner that runs months out needs to say which Tuesday.
    public static func heading(_ date: String, today: Date = .now, calendar: Calendar = .current) -> String {
        guard let day = DayString.date(date, calendar: calendar) else { return date }
        let full = day.formatted(.dateTime.weekday(.wide).month(.abbreviated).day())
        let days = calendar.dateComponents([.day], from: calendar.startOfDay(for: today), to: day).day ?? 0
        switch days {
        case 0: return "Today · \(full)"
        case 1: return "Tomorrow · \(full)"
        default:
            let sameYear = calendar.component(.year, from: day) == calendar.component(.year, from: today)
            return sameYear ? full : day.formatted(.dateTime.weekday(.wide).month(.abbreviated).day().year())
        }
    }

    /// What the editor holds after adding a note to a day that already has one.
    /// Typed text is appended rather than dropped — discarding what somebody just
    /// wrote to report a collision would be the worst of both.
    public static func merged(existing: String, typed: String) -> (body: String, appended: Bool) {
        let t = typed.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !t.isEmpty, t != existing.trimmingCharacters(in: .whitespacesAndNewlines) else { return (existing, false) }
        return ("\(existing)\n\(t)", true)
    }
}

@MainActor @Observable
public final class NotesModel {
    public private(set) var notes: [Note] = []
    public private(set) var hasLoaded = false
    public var errorMessage: String?
    private let client: APIClient

    public init(client: APIClient) { self.client = client }

    public func load() async {
        do { notes = try await client.notes() }
        catch { if !hasLoaded { errorMessage = error.localizedDescription } }
        hasLoaded = true
    }

    public func note(on date: String) -> Note? { notes.first { $0.date == date } }

    public enum SaveResult: Equatable { case saved, existsOnDay(Note), failed }

    /// Save. A day that already has a note is not an error to make the family
    /// resolve: the caller is handed that note so it can switch to editing it.
    public func save(date: String, body: String, editing note: Note?) async -> SaveResult {
        do {
            if let note { _ = try await client.updateNote(id: note.id, date: date == note.date ? nil : date, body: body) }
            else { _ = try await client.createNote(date: date, body: body) }
            await load()
            return .saved
        } catch APIError.conflict(_, let id) {
            await load()
            if let existing = notes.first(where: { $0.id == id }), existing.id != note?.id { return .existsOnDay(existing) }
            errorMessage = "That day already has a note."
            return .failed
        } catch {
            errorMessage = error.localizedDescription
            return .failed
        }
    }

    @discardableResult
    public func delete(_ note: Note) async -> Bool {
        do { try await client.deleteNote(id: note.id); await load(); return true }
        catch { errorMessage = error.localizedDescription; return false }
    }
}

extension APIClient {
    public func notes() async throws -> [Note] { try await get(.notes) }

    public func createNote(date: String, body: String) async throws -> Note {
        try await send(.createNote, body: PatchBody().set("date", Patch.value(date)).set("body", Patch.value(body)))
    }

    /// `date` only when the day moved, so a plain edit cannot trip the
    /// "already a note on that day" check against itself.
    public func updateNote(id: Int, date: String?, body: String) async throws -> Note {
        try await send(.updateNote, ["note_id": String(id)],
                       body: PatchBody().set("date", date.map { Patch.value($0) } ?? Patch<String>.unset).set("body", Patch.value(body)))
    }

    public func deleteNote(id: Int) async throws { try await perform(.deleteNote, ["note_id": String(id)]) }

    public func previousNotes(search: String, limit: Int, offset: Int) async throws -> ArchivePage<Note> {
        var query = [URLQueryItem(name: "limit", value: String(limit)), URLQueryItem(name: "offset", value: String(offset))]
        let term = search.trimmingCharacters(in: .whitespaces)
        if !term.isEmpty { query.append(URLQueryItem(name: "search", value: term)) }
        return try await get(.previousNotes, query: query)
    }
}
