# Written Reflection: Customer Support AI Agent
## Design Decision
One design decision I made was to preserve the customer's original question when retrieving and saving conversation memory. During implementation, I recognized that adding previously stored customer context directly to a query could affect what gets saved as the customer's original message. I adjusted the memory handling to retain the original query separately. This helps keep conversation history meaningful and allows the agent to personalize future responses without losing the context of what the customer actually asked.

# ----------------------------------------------------------------------------------------------------- #

## Challenge Encountered
The biggest challenge was getting the different AWS services to work together reliably. Although the agent could access order information and process refunds, some tools initially failed because of permission issues and incorrect invocation or response handling. I had to investigate IAM policies, review the Code Interpreter integration, and troubleshoot memory retrieval and saving. I also created and ran a Python test script to evaluate six scenarios, including order tracking, refunds, knowledge retrieval, memory, loyalty calculations, and browser automation. The tests helped identify which components were working and which still needed attention. In the latest test run, browser automation remained unavailable.

# ----------------------------------------------------------------------------------------------------- #

## Production Consideration
A key production consideration is ensuring that the agent does not perform sensitive customer operations based solely on generated responses. For example, before processing a refund, the system should verify the order, confirm eligibility, and prevent duplicate requests. Customer memory should also be isolated by customer identity to prevent information from one customer being exposed to another. In addition, monitoring and structured error handling would help identify service failures and prevent the agent from presenting unsuccessful operations as completed. These safeguards would improve the reliability, security, and trustworthiness of the system in a real customer support environment.

