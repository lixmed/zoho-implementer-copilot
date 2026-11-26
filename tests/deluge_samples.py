"""Deluge samples shared by tests."""

GOOD = """\
void automation.post_hot_lead(String lead_id)
{
	// Post a hot lead to Slack. Result map is logged so failures are diagnosable.
	result = Map();
	steps = List();
	lead = zoho.crm.getRecordById("Leads", lead_id.toLong());
	if(lead == null || lead.get("Lead_Temperature") == null)
	{
		info "lead or temperature missing for " + lead_id;
		return;
	}
	steps.add("fetched lead");
	if(lead.get("Lead_Temperature") != "Hot")
	{
		info "not hot, skipping";
		return;
	}
	message = Map();
	message.put("text","Hot lead: " + ifnull(lead.get("Last_Name"),"(no name)"));
	response = invokeurl
	[
		url :"https://slack.com/api/chat.postMessage"
		type :POST
		parameters:message.toString()
		connection:"slack_conn"
	];
	steps.add("posted to slack");
	result.put("status","ok");
	result.put("steps",steps);
	info result;
}
"""

BAD = """\
void automation.post_hot_lead(String lead_id)
{
	lead = zoho.crm.getRecordById("Leads", 4150868000000225013);
	temp = lead.get("Lead_Temprature");
	response = invokeurl
	[
		url :"https://slack.com/api/chat.postMessage"
		type :POST
		headers:{"Authorization":"Bearer xoxb-123456789012-abcdefghijkl"}
	];
	sendmail [ from: zoho.adminuserid to: "boss@acme-realty.test" subject: "hot" message: temp ];
"""
