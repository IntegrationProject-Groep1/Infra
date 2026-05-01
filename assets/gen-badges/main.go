// gen-badges — generates flat SVG status badges for the ShiftFestival Infra repo.
//
// Usage:
//
//	go run main.go                    # writes to ../badges/
//	go run main.go path/to/output/    # writes to a custom directory
//
// Add or edit entries in the `infraBadges` slice to customise badge output.
package main

import (
	"fmt"
	"os"
	"path/filepath"
	"text/template"
)

type badge struct {
	Label      string
	Value      string
	Color      string
	LabelWidth int
	ValueWidth int
}

func (b badge) Total() int   { return b.LabelWidth + b.ValueWidth }
func (b badge) LabelCX() int { return b.LabelWidth / 2 }
func (b badge) ValueCX() int { return b.LabelWidth + b.ValueWidth/2 }

const svgTpl = `<svg xmlns="http://www.w3.org/2000/svg" width="{{.Total}}" height="20" role="img" aria-label="{{.Label}}: {{.Value}}">
  <title>{{.Label}}: {{.Value}}</title>
  <clipPath id="r"><rect width="{{.Total}}" height="20" rx="3" fill="#fff"/></clipPath>
  <g clip-path="url(#r)">
    <rect width="{{.LabelWidth}}" height="20" fill="#0b1f2a"/>
    <rect x="{{.LabelWidth}}" width="{{.ValueWidth}}" height="20" fill="{{.Color}}"/>
  </g>
  <g fill="#fff" text-anchor="middle" font-family="DejaVu Sans,Verdana,Geneva,sans-serif" font-size="11">
    <text x="{{.LabelCX}}" y="14.5">{{.Label}}</text>
    <text x="{{.ValueCX}}" y="14.5">{{.Value}}</text>
  </g>
</svg>
`

func charWidth(c rune) int {
	switch {
	case c == 'i' || c == 'l' || c == '1' || c == '.' || c == ':' || c == '|':
		return 5
	case c == 'm' || c == 'w' || c == 'M' || c == 'W':
		return 9
	case c >= 'A' && c <= 'Z':
		return 8
	default:
		return 7
	}
}

func sectionWidth(s string) int {
	w := 10
	for _, c := range s {
		w += charWidth(c)
	}
	return w
}

var infraBadges = []badge{
	{Label: "namespace",  Value: "shift-festival", Color: "#6264a7"},
	{Label: "teams",      Value: "8 services",     Color: "#1A73E8"},
	{Label: "strategy",   Value: "GitOps",          Color: "#326CE5"},
	{Label: "rollback",   Value: "kubectl undo",    Color: "#0a7ea4"},
}

func main() {
	outDir := filepath.Join("..", "badges")
	if len(os.Args) > 1 {
		outDir = os.Args[1]
	}
	if err := os.MkdirAll(outDir, 0o755); err != nil {
		fmt.Fprintf(os.Stderr, "error: mkdir %s: %v\n", outDir, err)
		os.Exit(1)
	}

	tmpl := template.Must(template.New("badge").Parse(svgTpl))

	for _, b := range infraBadges {
		b.LabelWidth = sectionWidth(b.Label)
		b.ValueWidth = sectionWidth(b.Value)
		fname := filepath.Join(outDir, b.Label+".svg")
		f, err := os.Create(fname)
		if err != nil {
			fmt.Fprintf(os.Stderr, "error: create %s: %v\n", fname, err)
			continue
		}
		err = tmpl.Execute(f, b)
		f.Close()
		if err != nil {
			fmt.Fprintf(os.Stderr, "error: render %s: %v\n", fname, err)
			continue
		}
		fmt.Printf("  wrote %s  (%dpx)\n", fname, b.Total())
	}
}
