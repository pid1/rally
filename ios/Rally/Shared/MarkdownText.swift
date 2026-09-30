import RallyKit
import SwiftUI

/// A note's markdown, drawn natively. See `NoteMarkdown` for what it accepts.
struct MarkdownText: View {
    let markdown: String

    var body: some View {
        VStack(alignment: .leading, spacing: RallyDesign.space[1]) {
            ForEach(Array(NoteMarkdown.blocks(markdown).enumerated()), id: \.offset) { _, block in
                switch block {
                case .paragraph(let text):
                    Text(NoteMarkdown.inline(text))
                case .bullet(let text):
                    HStack(alignment: .firstTextBaseline, spacing: RallyDesign.space[2]) {
                        Text("•")
                        Text(NoteMarkdown.inline(text))
                    }
                case .numbered(let n, let text):
                    HStack(alignment: .firstTextBaseline, spacing: RallyDesign.space[2]) {
                        Text("\(n).").monospacedDigit()
                        Text(NoteMarkdown.inline(text))
                    }
                }
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }
}
