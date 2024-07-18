// This is a BAML file, which extends the Jinja2 templating language to write LLM functions.// Run a test to see how it works!
// How to call BAML functions from other languages: https://docs.boundaryml.com
// We want the LLM to extract this info from a climate report
class ClimateReport {
  source_name string
  date string @description("ISO8601 formatted date, when relative warming will cross 1.5 degree celcius")
  answer float @description("relative warming corresponding to year 2020")
}
// This is our LLM function we can call in Python or Typescript
// the receipt can be an image OR text here!
function ExtractClimareReport(climate_report: image | string) -> ClimateReport {
  // see clients.baml
  client GPT4o
  prompt #"
    {# start a user message #}
    {{ _.role("user") }}

    Extract info from this report:
    {{ climate_report }}

    {# special macro to print the output schema instructions. #}
    {{ ctx.output_format }}
  "#
}
// Test when the input is an image
test ClimateReportTest {
  functions [ExtractClimareReport]
  args {
    climate_report { url "https://www.wcrp-climate.org/images/news-new/2018/IPCC-SR15-current-and-stabilized-warming.jpg"}
  }
}