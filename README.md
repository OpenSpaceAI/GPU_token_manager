# GPU Resource Pool Management System

## Introduction
This document describes the GPU Resource Pool Management System, designed to efficiently allocate GPU resources among users, avoiding resource hogging and wastage. The system implements a token-based mechanism to ensure fair usage and incorporates monitoring to dynamically manage and adjust resource allocation.

## Background
With increasing demands on GPU resources leading to conflicts and inefficiencies, a new strategy for resource allocation was necessary. This management system transitions from group-based to individual-based resource management, introducing a token bucket strategy to handle GPU usage rights.

## System Overview

### Token Bucket Strategy
- Each user is assigned a token bucket that represents their GPU usage allowance.
- Tokens are consumed according to the GPU time utilized, with different GPUs having different costs per hour of usage.

### Pricing Model
- **Nvidia 2080**: 0.5 tokens per GPU-hour
- **Nvidia 3090**: 1 token per GPU-hour
- **Nvidia A6000**: 4 tokens per GPU-hour

### Example Usage Calculation
- **User Example**: For a user utilizing various GPUs in a month:
  - **Nvidia 2080**: 8 GPUs for 5 hours
  - **Nvidia 3090**: 4 GPUs for 10 hours
  - **Nvidia A6000**: 2 GPUs for 3 hours
  - **Total Cost**: `(0.5 * 8 * 5) + (1 * 4 * 10) + (4 * 2 * 3) = 84 tokens`
  - **End-of-Month Token Calculation**: `Initial tokens: 100 + Monthly addition: 30 - Consumption: 84 = 46 tokens remaining`

### Overdraft and Replenishment
- Users can go into a negative balance up to -10 tokens. Processes of users with negative balances may be terminated during high demand.
- The system replenishes 1 token per day to each user, allowing recovery from negative balances if no further consumption occurs.

## System Implementation

### Code Structure
The system leverages Python for backend processes, including scheduling tasks for token updates and utilization checks. It utilizes Prometheus for real-time data monitoring and logging to maintain records and operational transparency.

#### Main Components
- **Token Management**: Manages user tokens, saving state in a JSON file.
- **Usage Monitoring**: Queries GPU usage metrics from Prometheus and adjusts tokens accordingly.
- **Process Management**: In cases of high GPU utilization, processes belonging to users with negative token balances are terminated to free up resources.

### Scheduling
- Token updates and utilization checks are scheduled to run at regular intervals (every hour for token updates and every 30 minutes for utilization checks).

## Contributing
Contributions to the GPU Resource Pool Management System are welcome. Please fork the repository, make your changes, and submit a pull request for review.

