import Foundation
import Testing
@testable import RallyKit

struct NoteMarkdownTests {
    @Test func readsParagraphsBulletsAndNumbers() {
        let blocks = NoteMarkdown.blocks("Pizza night\n- dough\n* cheese\n1. bake\n2) eat")
        #expect(blocks == [.paragraph("Pizza night"), .bullet("dough"), .bullet("cheese"),
                           .numbered(1, "bake"), .numbered(2, "eat")])
    }

    @Test func aLineStartingWithBoldIsNotABullet() {
        #expect(NoteMarkdown.blocks("**Pizza** tonight") == [.paragraph("**Pizza** tonight")])
        #expect(NoteMarkdown.blocks("*note* this") == [.paragraph("*note* this")])
    }

    @Test func everyEnterIsALineBreakAndBlankLinesAreDropped() {
        #expect(NoteMarkdown.blocks("a\n\n\nb\r\nc").count == 3)
    }

    @Test func comparisonsSurvive() {
        #expect(NoteMarkdown.blocks("wear layers if temp < 40") == [.paragraph("wear layers if temp < 40")])
    }

    @Test func boldAndItalicAreStyledButLinksAndCodeAreNot() {
        let bold = NoteMarkdown.inline("**Pizza** night")
        #expect(String(bold.characters) == "Pizza night")
        #expect(bold.runs.contains { $0.inlinePresentationIntent?.contains(.stronglyEmphasized) == true })
        let link = NoteMarkdown.inline("[site](http://x.com) and `code`")
        #expect(String(link.characters) == "[site](http://x.com) and `code`")
        #expect(link.runs.allSatisfy { $0.link == nil })
    }
}

struct DashboardDecodingTests {
    @Test func decodesASnapshotWithANoteAndStem() throws {
        let json = Data(##"""
        {"has_snapshot":true,"generated_at":"2026-09-30T09:00:00Z","greeting":"Hi","weather_summary":"Sunny",
         "schedule":[{"time":"9:00 AM","title":"Dentist","notes":""}],"briefing":"",
         "stem_concept":{"title":"Buoyancy","field":"Science","explanation":"Float","activities":[{"idea":"Tub","audience":"kids"}]},
         "note":{"id":1,"date":"2026-09-30","body":"**Hi**","body_html":"<p><strong>Hi</strong></p>","created_at":"2026-09-30T08:00:00","updated_at":"2026-09-30T08:00:00"}}
        """##.utf8)
        let d = try JSONDecoder.rally.decode(Dashboard.self, from: json)
        #expect(d.hasSnapshot && d.schedule.first?.title == "Dentist" && d.note?.body == "**Hi**")
        #expect(d.stemConcept?.activities.first?.idea == "Tub")
    }

    @Test func decodesTheNoSnapshotAnswer() throws {
        let json = Data(#"{"has_snapshot":false,"generated_at":null,"greeting":"None yet","weather_summary":"Run generate","schedule":[],"briefing":"","stem_concept":null,"note":null}"#.utf8)
        let d = try JSONDecoder.rally.decode(Dashboard.self, from: json)
        #expect(!d.hasSnapshot && d.generatedAt == nil && d.note == nil)
    }
}
